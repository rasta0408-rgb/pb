"""Telegram module for creating Playerok product drafts from JSON payloads."""

from __future__ import annotations

import html
import json
from types import SimpleNamespace

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from settings import Settings as sett


PREFIX = "[product creator]"
VERSION = "1.0.0"
NAME = "Product creator"
DESCRIPTION = "Создание черновиков товаров Playerok и публикация только после подтверждения"
AUTHORS = "rasta0408-rgb"
LINKS = "https://github.com/rasta0408-rgb/pb"

BOT_EVENT_HANDLERS = {}
PLAYEROK_EVENT_HANDLERS = {}
router = Router()

from .yang_wizard import router as yang_router

router.include_router(yang_router)
TELEGRAM_BOT_ROUTERS = [router]


class ProductCreatorStates(StatesGroup):
    waiting_for_payload = State()


def _is_authorized(user_id: int) -> bool:
    config = sett.get("config")
    return user_id in config["telegram"]["bot"]["signed_users"]


async def _require_authorization(message_or_callback: types.Message | types.CallbackQuery) -> bool:
    user = message_or_callback.from_user
    if _is_authorized(user.id):
        return True

    if isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.answer("Нет доступа к этой команде.", show_alert=True)
    else:
        await message_or_callback.answer("Нет доступа к этой команде.")
    return False


def _payload_example(category_id: str, obtaining_type_id: str, options: list, data_fields: list) -> str:
    attributes = {
        option.field: option.value
        for option in options
    }
    item_data_fields = [
        {"id": field.id, "value": ""}
        for field in data_fields
        if getattr(getattr(field, "type", None), "name", None) == "ITEM_DATA" and field.required
    ]
    return json.dumps(
        {
            "game_category_id": category_id,
            "obtaining_type_id": obtaining_type_id,
            "name": "Название товара",
            "price": 100,
            "description": "Описание товара",
            "attributes": attributes,
            "data_fields": item_data_fields,
        },
        ensure_ascii=False,
        indent=2,
    )


def _parse_payload(raw_payload: str) -> dict:
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"Некорректный JSON: {error.msg}") from error

    required = (
        "game_category_id",
        "obtaining_type_id",
        "name",
        "price",
        "description",
    )
    missing = [name for name in required if not payload.get(name)]
    if missing:
        raise ValueError("Не заполнены поля: " + ", ".join(missing))

    if not isinstance(payload["price"], int) or isinstance(payload["price"], bool) or payload["price"] <= 0:
        raise ValueError("price должен быть положительным целым числом")

    attributes = payload.get("attributes", {})
    data_fields = payload.get("data_fields", [])
    if not isinstance(attributes, dict):
        raise ValueError("attributes должен быть объектом JSON")
    if not isinstance(data_fields, list):
        raise ValueError("data_fields должен быть массивом JSON")

    for field in data_fields:
        if not isinstance(field, dict) or not field.get("id") or "value" not in field:
            raise ValueError("Каждый элемент data_fields должен содержать id и value")

    return payload


def _draft_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📝 Создать черновик", callback_data="product_create_draft"),
            InlineKeyboardButton(text="✖️ Отмена", callback_data="product_cancel"),
        ]
    ])


@router.message(Command("product_schema"))
async def product_schema(message: types.Message):
    """Show a category-specific JSON example.

    Usage: /product_schema <category_id> <obtaining_type_id>
    """
    if not await _require_authorization(message):
        return

    _, _, arguments = (message.text or "").partition(" ")
    category_id, *rest = arguments.split()
    obtaining_type_id = rest[0] if rest else None
    if not category_id or not obtaining_type_id:
        await message.answer(
            "Использование: /product_schema <ID_категории> <ID_типа_получения>\n"
            "ID категории и типа можно получить из Playerok."
        )
        return

    try:
        from plbot.playerokbot import get_playerok_bot as plbot

        account = plbot().account
        category = account.get_game_category(id=category_id)
        data_fields = account.get_game_category_data_fields(
            category_id,
            obtaining_type_id,
            count=100,
        ).data_fields
        example = _payload_example(
            category.id,
            obtaining_type_id,
            category.options or [],
            data_fields,
        )
        await message.answer(
            f"Шаблон для категории «{html.escape(category.name)}».\n"
            "Заполните значения и отправьте /product_create, затем сам JSON отдельным сообщением:\n\n"
            f"<pre>{html.escape(example)}</pre>",
            parse_mode="HTML",
        )
    except Exception as error:
        await message.answer(f"Не удалось получить схему категории: {html.escape(str(error))}", parse_mode="HTML")


@router.message(Command("product_create"))
async def product_create(message: types.Message, state: FSMContext):
    """Start a draft-creation flow."""
    if not await _require_authorization(message):
        return

    await state.clear()
    await state.set_state(ProductCreatorStates.waiting_for_payload)
    await message.answer(
        "Отправьте JSON товара одним сообщением.\n"
        "Для шаблона используйте: /product_schema <ID_категории> <ID_типа_получения>\n\n"
        "Товар будет создан только как черновик. Публикация появится отдельным действием."
    )


@router.message(ProductCreatorStates.waiting_for_payload, F.text)
async def product_payload(message: types.Message, state: FSMContext):
    if not await _require_authorization(message):
        return

    try:
        payload = _parse_payload(message.text)
        await state.update_data(product_payload=payload)
        await state.set_state(None)
        await message.answer(
            "Проверьте черновик:\n"
            f"• Название: {html.escape(payload['name'])}\n"
            f"• Цена: {payload['price']} ₽\n"
            f"• Категория: {html.escape(payload['game_category_id'])}\n"
            f"• Тип получения: {html.escape(payload['obtaining_type_id'])}\n\n"
            "Создать черновик?",
            reply_markup=_draft_keyboard(),
        )
    except ValueError as error:
        await message.answer(f"Данные не приняты: {html.escape(str(error))}", parse_mode="HTML")


@router.callback_query(F.data == "product_cancel")
async def product_cancel(callback: types.CallbackQuery, state: FSMContext):
    if not await _require_authorization(callback):
        return

    await state.clear()
    await callback.message.edit_text("Создание товара отменено.")
    await callback.answer()


@router.callback_query(F.data == "product_create_draft")
async def product_create_draft(callback: types.CallbackQuery, state: FSMContext):
    if not await _require_authorization(callback):
        return

    data = await state.get_data()
    payload = data.get("product_payload")
    if not payload:
        await callback.answer("Черновик устарел. Запустите /product_create ещё раз.", show_alert=True)
        return

    try:
        from plbot.playerokbot import get_playerok_bot as plbot

        account = plbot().account
        options = [
            SimpleNamespace(field=field, value=value)
            for field, value in payload.get("attributes", {}).items()
        ]
        data_fields = [
            SimpleNamespace(id=field["id"], value=field["value"])
            for field in payload.get("data_fields", [])
        ]
        item = account.create_item(
            game_category_id=payload["game_category_id"],
            obtaining_type_id=payload["obtaining_type_id"],
            name=payload["name"],
            price=payload["price"],
            description=payload["description"],
            options=options,
            data_fields=data_fields,
            attachments=[],
        )
        await state.update_data(product_item=item)
        await callback.message.edit_text(
            "✅ Черновик создан.\n"
            f"Название: {html.escape(item.name)}\n"
            f"ID: <code>{html.escape(item.id)}</code>\n\n"
            "Он ещё не опубликован.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Перейти к публикации", callback_data="product_select_priority")],
                [InlineKeyboardButton(text="Оставить черновиком", callback_data="product_keep_draft")],
            ]),
        )
        await callback.answer()
    except Exception as error:
        await callback.message.edit_text(
            f"Не удалось создать черновик: {html.escape(str(error))}",
            parse_mode="HTML",
        )
        await callback.answer()


@router.callback_query(F.data == "product_keep_draft")
async def product_keep_draft(callback: types.CallbackQuery, state: FSMContext):
    if not await _require_authorization(callback):
        return

    await state.clear()
    await callback.message.edit_text("Черновик сохранён и не опубликован.")
    await callback.answer()


@router.callback_query(F.data == "product_select_priority")
async def product_select_priority(callback: types.CallbackQuery, state: FSMContext):
    if not await _require_authorization(callback):
        return

    data = await state.get_data()
    item = data.get("product_item")
    if not item:
        await callback.answer("Черновик устарел. Запустите /product_create ещё раз.", show_alert=True)
        return

    try:
        from plbot.playerokbot import get_playerok_bot as plbot

        statuses = plbot().account.get_item_priority_statuses(item.id, item.raw_price)
        if not statuses:
            raise ValueError("Playerok не вернул доступные статусы приоритета")

        await state.update_data(product_priority_statuses=statuses)
        buttons = []
        for index, status in enumerate(statuses):
            title = "Бесплатный" if status.price == 0 else f"Премиум — {status.price} ₽"
            buttons.append([InlineKeyboardButton(text=title, callback_data=f"product_priority:{index}")])
        buttons.append([InlineKeyboardButton(text="Оставить черновиком", callback_data="product_keep_draft")])
        await callback.message.edit_text(
            "Выберите приоритет для публикации:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await callback.answer()
    except Exception as error:
        await callback.message.edit_text(
            f"Не удалось подготовить публикацию: {html.escape(str(error))}",
            parse_mode="HTML",
        )
        await callback.answer()


@router.callback_query(F.data.startswith("product_priority:"))
async def product_confirm_publish(callback: types.CallbackQuery, state: FSMContext):
    if not await _require_authorization(callback):
        return

    try:
        index = int((callback.data or "").split(":", maxsplit=1)[1])
        data = await state.get_data()
        statuses = data.get("product_priority_statuses") or []
        item = data.get("product_item")
        status = statuses[index]
        if not item:
            raise ValueError("Черновик не найден")

        await state.update_data(product_priority_status_id=status.id)
        title = "бесплатный" if status.price == 0 else f"премиум ({status.price} ₽)"
        await callback.message.edit_text(
            f"Опубликовать «{html.escape(item.name)}» с приоритетом: {title}?\n"
            "Это действие выставит товар на продажу.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Опубликовать", callback_data="product_publish_confirm")],
                [InlineKeyboardButton(text="✖️ Оставить черновиком", callback_data="product_keep_draft")],
            ]),
        )
        await callback.answer()
    except (IndexError, ValueError) as error:
        await callback.answer(f"Некорректный выбор: {error}", show_alert=True)


@router.callback_query(F.data == "product_publish_confirm")
async def product_publish(callback: types.CallbackQuery, state: FSMContext):
    if not await _require_authorization(callback):
        return

    data = await state.get_data()
    item = data.get("product_item")
    status_id = data.get("product_priority_status_id")
    if not item or not status_id:
        await callback.answer("Черновик устарел. Запустите /product_create ещё раз.", show_alert=True)
        return

    try:
        from plbot.playerokbot import get_playerok_bot as plbot

        published = plbot().account.publish_item(item.id, status_id)
        await state.clear()
        await callback.message.edit_text(
            f"✅ Товар опубликован: {html.escape(published.name)}",
            parse_mode="HTML",
        )
        await callback.answer()
    except Exception as error:
        await callback.message.edit_text(
            f"Не удалось опубликовать товар: {html.escape(str(error))}",
            parse_mode="HTML",
        )
        await callback.answer()
