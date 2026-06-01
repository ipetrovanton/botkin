"""Команда /dynamics — график динамики показателя."""
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, BufferedInputFile

from backend.db.connection import get_conn
from backend.db.queries import lab_dynamics
from bot_and_rag.viz.plots import lab_dynamics_chart

router = Router(name="dynamics")


@router.message(Command("dynamics"))
async def cmd_dynamics(message: Message, command: CommandObject) -> None:
    analyte = (command.args or "").strip()
    if not analyte:
        await message.answer("Использование: /dynamics холестерин")
        return

    tg_user_id = message.from_user.id
    with get_conn() as conn:
        user = conn.execute(
            "SELECT id FROM users WHERE telegram_user_id = ?", (tg_user_id,)
        ).fetchone()
        if not user:
            await message.answer("⚠️ Отправь /start для регистрации.")
            return
        user_id = user["id"]

    points = lab_dynamics(user_id, analyte, limit=30)
    if not points:
        await message.answer(
            f"❌ Данных по «{analyte}» нет. Загрузи анализ с этим показателем."
        )
        return

    png = lab_dynamics_chart(points, analyte)
    last = points[-1]
    summary = (
        f"📊 «{analyte}», последнее значение: "
        f"<b>{last['value_num']} {last['unit']}</b>"
    )
    if last.get("ref_low") is not None and last.get("ref_high") is not None:
        summary += f"\nНорма: {last['ref_low']}–{last['ref_high']} {last['unit']}"

    await message.answer_photo(
        photo=BufferedInputFile(png, filename=f"dynamics_{analyte}.png"),
        caption=summary,
    )