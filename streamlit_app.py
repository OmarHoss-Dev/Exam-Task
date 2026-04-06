"""
Arabic exam — Streamlit entry (uses same questions.json + logic as the Node app).
Deploy on Streamlit Cloud: Main file = streamlit_app.py, requirements = requirements-streamlit.txt,
Secrets: ADMIN_TOKEN = "<long secret>"

Student links: add ?v=1 .. ?v=7  (example: https://YOUR_APP.streamlit.app?v=3)
Admin: https://YOUR_APP.streamlit.app?admin=1&token=YOUR_ADMIN_TOKEN
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from exam_logic import grade, load_bank, normalize_key, ordered_ids_for_variant

ROOT = Path(__file__).resolve().parent


def _db_path() -> Path:
    env = os.environ.get("EXAM_DB_PATH")
    if env:
        return Path(env)
    if os.environ.get("STREAMLIT_CLOUD", "") or os.environ.get("STREAMLIT_SHARING", ""):
        return Path("/tmp/exam.sqlite")
    return ROOT / "data" / "exam.sqlite"


DB_PATH = _db_path()


def _rtl_css() -> None:
    st.markdown(
        """
<style>
  .stApp, .stMarkdown, label, p { direction: rtl; text-align: right; }
  div[data-testid="stRadio"] label { font-size: 1.1rem !important; }
</style>
""",
        unsafe_allow_html=True,
    )


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS pending (k TEXT PRIMARY KEY)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS submissions (
            id TEXT PRIMARY KEY,
            submitted_at TEXT NOT NULL,
            participant_key TEXT NOT NULL,
            full_name TEXT NOT NULL,
            section TEXT NOT NULL,
            variant INTEGER NOT NULL,
            total_marks INTEGER NOT NULL,
            max_marks INTEGER NOT NULL,
            wrong_json TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()


def _admin_token() -> str:
    try:
        return str(st.secrets["ADMIN_TOKEN"])
    except Exception:
        return os.environ.get("ADMIN_TOKEN", "")


def _header_html(bank: dict) -> str:
    h = bank.get("examHeader") or {}
    parts = []
    if h.get("line1"):
        parts.append(f"<p style='font-weight:700;font-size:1.15rem'>{h['line1']}</p>")
    if h.get("line2"):
        parts.append(f"<p>{h['line2']}</p>")
    if h.get("scoreLine"):
        parts.append(f"<p style='color:#2ecc71'>{h['scoreLine']}</p>")
    if h.get("instruction"):
        parts.append(f"<p style='color:#888'>{h['instruction']}</p>")
    return "<div>" + "".join(parts) + "</div>"


def _variant_from_url() -> int:
    try:
        p = st.query_params
        raw = p.get("v")
        if raw is None:
            raw = p.get("variant")
        if raw is None:
            return 1
        v = int(str(raw))
        return min(7, max(1, v))
    except Exception:
        return 1


def _admin_page(bank: dict) -> None:
    st.title("Admin — submissions")
    tok = _admin_token()
    if not tok:
        st.error("Set ADMIN_TOKEN in Streamlit secrets (or env).")
        return
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT submitted_at, full_name, section, variant, total_marks, max_marks, wrong_json FROM submissions ORDER BY submitted_at DESC"
    ).fetchall()
    conn.close()
    by_id = {q["id"]: q for q in bank["questions"]}
    for sub_at, fn, sec, var, tot, mx, wj in rows:
        wrong = json.loads(wj)
        st.markdown(f"**{fn}** — شعبة: {sec} — رابط v={var} — **{tot}/{mx}** — `{sub_at}`")
        if wrong:
            for w in wrong:
                q = by_id.get(w["questionId"], {})
                preview = (q.get("text") or w["questionId"])[:120]
                reason = "empty" if w["reason"] == "empty" else "wrong"
                st.caption(f"  • Q{w['orderIndex']} ({reason}): {preview}")
        st.divider()


def _page_intro(bank: dict, variant: int) -> None:
    st.markdown(_header_html(bank), unsafe_allow_html=True)
    st.subheader("تعليمات مهمة قبل البدء")
    st.markdown(
        """
- نوع الأسئلة: **صح أو خطأ**.
- لا يمكن الرجوع لسؤال سابق.
- **٣٠ ثانية** لكل سؤال ثم الانتقال تلقائياً.
- **محاولة واحدة** لكل (اسم الطالبه + الشعبة).
- ترك السؤال بلا إجابة = **خطأ**.
- لن تُعرض **الدرجة** بعد الانتهاء.
"""
    )
    name = st.text_input("اسم الطالبه")
    section = st.text_input("الشعبة")
    if st.button("بدء الاختبار", type="primary"):
        if not name.strip() or not section.strip():
            st.error("يرجى إدخال اسم الطالبه والشعبة.")
            return
        key = normalize_key(name, section)
        conn = sqlite3.connect(DB_PATH)
        if conn.execute("SELECT 1 FROM pending WHERE k = ?", (key,)).fetchone():
            conn.close()
            st.error("لا يمكنك دخول الاختبار أكثر من مرة (بداية مسجّلة أو إرسال سابق).")
            return
        if conn.execute("SELECT 1 FROM submissions WHERE participant_key = ?", (key,)).fetchone():
            conn.close()
            st.error("لا يمكنك دخول الاختبار أكثر من مرة.")
            return
        conn.execute("INSERT INTO pending (k) VALUES (?)", (key,))
        conn.commit()
        conn.close()

        try:
            oids = ordered_ids_for_variant(bank, variant)
        except Exception as e:
            st.error(str(e))
            return

        qs = []
        for qid in oids:
            q = next(x for x in bank["questions"] if x["id"] == qid)
            qs.append({"id": qid, "text": q["text"], "options": q["options"]})

        st.session_state.exam_phase = "exam"
        st.session_state.variant = variant
        st.session_state.full_name = name.strip()
        st.session_state.section = section.strip()
        st.session_state.participant_key = key
        st.session_state.ordered_ids = oids
        st.session_state.questions = qs
        st.session_state.q_index = 0
        st.session_state.answers = {}
        st.session_state.q_started = time.time()
        st.rerun()


def _page_exam(bank: dict) -> None:
    st_autorefresh(interval=1000, limit=None, key="exam_tick")

    qs = st.session_state.questions
    i = int(st.session_state.q_index)
    if i >= len(qs):
        _finalize_submission(bank)
        return

    elapsed = time.time() - float(st.session_state.q_started)
    remain = max(0, int(30 - elapsed))

    if elapsed >= 30:
        qid = qs[i]["id"]
        st.session_state.answers.setdefault(qid, None)
        st.session_state.q_index = i + 1
        st.session_state.q_started = time.time()
        st.rerun()
        return

    st.markdown(_header_html(bank), unsafe_allow_html=True)
    st.caption(f"السؤال {i + 1} من {len(qs)} — الوقت المتبقي: **{remain}** ثانية")

    q = qs[i]
    st.markdown(f"**{q['text']}**")
    opts = q["options"]
    choice = st.radio(
        "اختر الإجابة",
        options=list(range(len(opts))),
        format_func=lambda ix: opts[ix],
        key=f"ans_{i}",
        horizontal=True,
        label_visibility="collapsed",
        index=None,
    )
    if choice is not None:
        st.session_state.answers[q["id"]] = int(choice)


def _finalize_submission(bank: dict) -> None:
    key = st.session_state.participant_key
    ordered = list(st.session_state.ordered_ids)
    answers = {}
    for qid in ordered:
        answers[qid] = st.session_state.answers.get(qid)

    result = grade(ordered, answers, bank)
    row_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM pending WHERE k = ?", (key,))
    conn.execute(
        """INSERT INTO submissions
        (id, submitted_at, participant_key, full_name, section, variant, total_marks, max_marks, wrong_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row_id,
            now,
            key,
            st.session_state.full_name,
            st.session_state.section,
            int(st.session_state.variant),
            result["total"],
            result["max"],
            json.dumps(result["wrong"], ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()

    st.session_state.exam_phase = "done"
    st.rerun()


def _page_done() -> None:
    st.success("تم الإرسال. شكراً — لن تُعرض الدرجة هنا.")


def main() -> None:
    st.set_page_config(page_title="اختبار", layout="centered")
    _rtl_css()
    _init_db()
    bank = load_bank(ROOT)

    if st.query_params.get("admin") == "1":
        tok_in = st.query_params.get("token", "")
        tok_ok = _admin_token()
        if tok_ok and tok_in == tok_ok:
            _admin_page(bank)
            return
        st.error("Unauthorized admin access.")
        return

    variant = _variant_from_url()
    if "exam_phase" not in st.session_state:
        st.session_state.exam_phase = "intro"

    phase = st.session_state.exam_phase
    if phase == "intro":
        _page_intro(bank, variant)
    elif phase == "exam":
        _page_exam(bank)
    else:
        _page_done()


if __name__ == "__main__":
    main()
