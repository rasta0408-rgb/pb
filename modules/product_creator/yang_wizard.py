"""Guided Telegram flow for Playerok: Metin 2 → Янги."""

from __future__ import annotations

import html
import os
from tempfile import NamedTemporaryFile

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from playerokapi.enums import GameCategoryDataFieldTypes
from settings import Settings as sett


router = Router()


class YangStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_price = State()
    waiting_for_description = State()
    waiting_for_item_data = State()
    waiting_for_images = State()


def _authorized(user_id: int) -> bool:
    return user_id in sett.get("config")["telegram"]["bot"]["signed_users"]


async def _deny_if_needed(event: types.Message | types.CallbackQuery) -> bool:
    if _authorized(event.from_user.id):
        return False
    if isinstance(event, types.CallbackQuery):
        await event.answer("Нет доступа к этой команде.", show_alert=True)
    else:
        await event.answer("Нет доступа к этой команде.")
    return True


async def _delete_temp_images(paths: list[str]) -> None:
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass


async def _get_yangs_category():
    from plbot.playerokbot import get_playerok_bot as plbot

    account = plbot().account
    game = account.get_game(slug="metin-2")
    category = next((item for item in game.categories if item.slug == "yangs"), None)
    if category is None:
        raise ValueError("Категория «Янги» сейчас не найдена в Metin 2.")
    return account, category


def _button_rows(items: list, prefix: str, label) -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(text=label(item), callback_data=f"{prefix}:{index}")]
        for index, item in enumerate(items)
    ]


@router.message(Command("yang_create"))
async def yang_create(message: types.Message, state: FSMContext):
    """Start creating a Metin 2 Yang draft."""
    if await _deny_if_needed(message):
        return

    await state.clear()
    try:
        account, category = await _get_yangs_category()
        obtaining_types = account.get_game_category_obtaining_types(
            category.id,
            count=100,
        ).obtaining_types
        if not obtaining_types:
            raise ValueError("Playerok не вернул способы получения для этой категории.")

        await state.update_data(
            yang_category_id=category.id,
            yang_category_name=category.name,
            yang_obtaining_types=obtaining_types,
            yang_images=[],
        )
        rows = _button_rows(
            obtaining_types,
            "yang_obtaining",
            lambda item: item.name,
        )
        await message.answer(
            "Metin 2 → Янги\n\nВыберите способ получения товара:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception as error:
        await message.answer(
            f"Не удалось открыть категорию «Янги»: {html.escape(str(error))}",
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("yang_obtaining:"))
async def yang_choose_obtaining(callback: types.CallbackQuery, state: FSMContext):
    if await _deny_if_needed(callback):
        return

    try:
        index = int((callback.data or "").split(":", maxsplit=1)[1])
        data = await state.get_data()
        obtaining_type = (data.get("yang_obtaining_types") or [])[index]

        from plbot.playerokbot import get_playerok_bot as plbot

        account = plbot().account
        fields = account.get_game_category_data_fields(
            data["yang_category_id"],
            obtaining_type.id,
            count=100,
        ).data_fields
        item_fields = [
            field for field in fields
            if field.type == GameCategoryDataFieldTypes.ITEM_DATA
        ]

        await state.update_data(
            yang_obtaining_type_id=obtaining_type.id,
            yang_item_fields=item_fields,
            yang_item_field_values=[],
        )
        await state.set_state(YangStates.waiting_for_name)
        await callback.message.edit_text(
            f"Способ получения: {html.escape(obtaining_type.name)}\n\n"
            "Введите название товара:",
            parse_mode="HTML",
        )
        await callback.answer()
    except (IndexError, ValueError, KeyError) as error:
        await callback.answer(f"Не удалось выбрать способ получения: {error}", show_alert=True)


@router.message(YangStates.waiting_for_name, F.text)
async def yang_name(message: types.Message, state: FSMContext):
    if await _deny_if_needed(message):
        return

    name = message.text.strip()
    if not name:
        await message.answer("Название не должно быть пустым. Введите его ещё раз:")
        return
    if len(name) > 140:
        await message.answer("Название слишком длинное. Максимум 140 символов:")
        return

    await state.update_data(yang_name=name)
    await state.set_state(YangStates.waiting_for_price)
    await message.answer("Введите цену в рублях целым числом:")


@router.message(YangStates.waiting_for_price, F.text)
async def yang_price(message: types.Message, state: FSMContext):
    if await _deny_if_needed(message):
        return

    raw_price = message.text.strip()
    if not raw_price.isdigit() or int(raw_price) <= 0:
        await message.answer("Цена должна быть положительным целым числом. Введите её ещё раз:")
        return

    await state.update_data(yang_price=int(raw_price))
    await state.set_state(YangStates.waiting_for_description)
    await message.answer("Введите описание товара:")


@router.message(YangStates.waiting_for_description, F.text)
async def yang_description(message: types.Message, state: FSMContext):
    if await _deny_if_needed(message):
        return

    description = message.text.strip()
    if not description:
        await message.answer("Описание не должно быть пустым. Введите его ещё раз:")
        return

    data = await state.get_data()
    fields = data.get("yang_item_fields") or []
    await state.update_data(yang_description=description)

    if fields:
        await state.set_state(YangStates.waiting_for_item_data)
        first = fields[0]
        await message.answer(
            f"Заполните поле «{html.escape(first.label)}»:",
            parse_mode="HTML",
        )
        return

    await state.set_state(YangStates.waiting_for_images)
    await message.answer(
        "Пришлите изображения товара — по одному или альбомом.\n"
        "Когда закончите, отправьте /yang_done. Изображения необязательны."
    )


@router.message(YangStates.waiting_for_item_data, F.text)
async def yang_item_data(message: types.Message, state: FSMContext):
    if await _deny_if_needed(message):
        return

    value = message.text.strip()
    data = await state.get_data()
    fields = data.get("yang_item_fields") or []
    values = data.get("yang_item_field_values") or []
    field_index = len(values)
    current_field = fields[field_index]

    if current_field.required and not value:
        await message.answer(f"Поле «{html.escape(current_field.label)}» обязательно. Введите значение:")
        return

    values.append({"id": current_field.id, "value": value})
    await state.update_data(yang_item_field_values=values)

    next_index = field_index + 1
    if next_index < len(fields):
        next_field = fields[next_index]
        await message.answer(f"Заполните поле «{html.escape(next_field.label)}»:", parse_mode="HTML")
        return

    await state.set_state(YangStates.waiting_for_images)
    await message.answer(
        "Пришлите изображения товара — по одному или альбомом.\n"
        "Когда закончите, отправьте /yang_done. Изображения необязательны."
    )


@router.message(YangStates.waiting_for_images, F.photo)
async def yang_image(message: types.Message, state: FSMContext):
    if await _deny_if_needed(message):
        return

    with NamedTemporaryFile(delete=False, suffix=".jpg") as temporary:
        await message.bot.download(message.photo[-1], destination=temporary.name)
        path = temporary.name

    data = await state.get_data()
    images = data.get("yang_images") or []
    images.append(path)
    await state.update_data(yang_images=images)
    await message.answer(f"Изображение добавлено ({len(images)}). Пришлите ещё или отправьте /yang_done.")


@router.message(YangStates.waiting_for_images, Command("yang_done"))
async def yang_done(message: types.Message, state: FSMContext):
    if await _deny_if_needed(message):
        return

    data = await state.get_data()
    await state.set_state(None)
    await message.answer(
        "Проверьте черновик:\n"
        f"• Название: {html.escape(data['yang_name'])}\n"
        f"• Цена: {data['yang_price']} ₽\n"
        f"• Изображений: {len(data.get('yang_images') or [])}\n\n"
        "Создать черновик?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 Создать черновик", callback_data="yang_create_draft")],
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="yang_cancel")],
        ]),
    )


@router.callback_query(F.data == "yang_cancel")
async def yang_cancel(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await _delete_temp_images(data.get("yang_images") or [])
    await state.clear()
    await callback.message.edit_text("Создание товара отменено.")
    await callback.answer()


@router.callback_query(F.data == "yang_create_draft")
async def yang_create_draft(callback: types.CallbackQuery, state: FSMContext):
    if await _deny_if_needed(callback):
        return

    data = await state.get_data()
    images = data.get("yang_images") or []
    try:
        account, category = await _get_yangs_category()
        if category.options:
            raise ValueError(
                "В категории появились дополнительные параметры. "
                "Откройте новый сценарий создания после обновления модуля."
            )

        from types import SimpleNamespace
        item = account.create_item(
            game_category_id=category.id,
            obtaining_type_id=data["yang_obtaining_type_id"],
            name=data["yang_name"],
            price=data["yang_price"],
            description=data["yang_description"],
            options=[],
            data_fields=[
                SimpleNamespace(id=field["id"], value=field["value"])
                for field in data.get("yang_item_field_values") or []
            ],
            attachments=images,
        )
        await _delete_temp_images(images)
        await state.update_data(yang_images=[], yang_item=item)
        await callback.message.edit_text(
            f"✅ Черновик создан: {html.escape(item.name)}\n\n"
            "Он не опубликован. Перейти к публикации?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Перейти к публикации", callback_data="yang_select_priority")],
                [InlineKeyboardButton(text="Оставить черновиком", callback_data="yang_keep_draft")],
            ]),
        )
        await callback.answer()
    except Exception as error:
        await _delete_temp_images(images)
        await state.update_data(yang_images=[])
        await callback.message.edit_text(
            f"Не удалось создать черновик: {html.escape(str(error))}",
            parse_mode="HTML",
        )
        await callback.answer()


@router.callback_query(F.data == "yang_keep_draft")
async def yang_keep_draft(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Черновик сохранён и не опубликован.")
    await callback.answer()


@router.callback_query(F.data == "yang_select_priority")
async def yang_select_priority(callback: types.CallbackQuery, state: FSMContext):
    if await _deny_if_needed(callback):
        return

    data = await state.get_data()
    item = data.get("yang_item")
    if not item:
        await callback.answer("Черновик не найден. Создайте его ещё раз.", show_alert=True)
        return

    try:
        from plbot.playerokbot import get_playerok_bot as plbot

        statuses = plbot().account.get_item_priority_statuses(item.id, item.raw_price)
        await state.update_data(yang_priority_statuses=statuses)
        rows = _button_rows(
            statuses,
            "yang_priority",
            lambda status: "Бесплатный" if status.price == 0 else f"Премиум — {status.price} ₽",
        )
        rows.append([InlineKeyboardButton(text="Оставить черновиком", callback_data="yang_keep_draft")])
        await callback.message.edit_text(
            "Выберите приоритет для публикации:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await callback.answer()
    except Exception as error:
        await callback.message.edit_text(
            f"Не удалось подготовить публикацию: {html.escape(str(error))}",
            parse_mode="HTML",
        )
        await callback.answer()


@router.callback_query(F.data.startswith("yang_priority:"))
async def yang_confirm_publish(callback: types.CallbackQuery, state: FSMContext):
    if await _deny_if_needed(callback):
        return

    try:
        index = int((callback.data or "").split(":", maxsplit=1)[1])
        data = await state.get_data()
        status = (data.get("yang_priority_statuses") or [])[index]
        item = data.get("yang_item")
        if not item:
            raise ValueError("Черновик не найден")

        await state.update_data(yang_priority_status_id=status.id)
        priority = "бесплатный" if status.price == 0 else f"премиум ({status.price} ₽)"
        await callback.message.edit_text(
            f"Опубликовать «{html.escape(item.name)}» с приоритетом: {priority}?\n"
            "Это действие выставит товар на продажу.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Опубликовать", callback_data="yang_publish_confirm")],
                [InlineKeyboardButton(text="✖️ Оставить черновиком", callback_data="yang_keep_draft")],
            ]),
        )
        await callback.answer()
    except (IndexError, ValueError) as error:
        await callback.answer(f"Некорректный выбор: {error}", show_alert=True)


@router.callback_query(F.data == "yang_publish_confirm")
async def yang_publish(callback: types.CallbackQuery, state: FSMContext):
    if await _deny_if_needed(callback):
        return

    data = await state.get_data()
    item = data.get("yang_item")
    status_id = data.get("yang_priority_status_id")
    if not item or not status_id:
        await callback.answer("Черновик не найден. Создайте его ещё раз.", show_alert=True)
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
