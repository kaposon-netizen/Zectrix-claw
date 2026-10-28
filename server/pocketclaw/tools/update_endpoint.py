import re
from pathlib import Path
from make_portal_html import portal_html

endpoint_path = Path(__file__).resolve().parent.parent / "endpoint.py"
content = endpoint_path.read_text(encoding="utf-8")

# 1. Replace _HTML_PARENT_PORTAL
pattern = r'_HTML_PARENT_PORTAL = """.*?"""\n\n\nclass _Caller:'
replacement = f'_HTML_PARENT_PORTAL = """{portal_html}"""\n\n\nclass _Caller:'

new_content, count = re.subn(pattern, replacement, content, count=1, flags=re.DOTALL)
if count != 1:
    print(f"Failed to replace _HTML_PARENT_PORTAL, count={count}")
    exit(1)

# 2. Add MP endpoints before return
mp_endpoints = """
        return {
            "ok": True,
            "child_id": child,
            "tasks_added": tasks_added,
            "dictation_count": dictation_count,
            "stats": notes.task_stats(child)
        }

    def _resolve_child(child_param: Optional[str] = None) -> str:
        if child_param and child_param.strip():
            return child_param.strip()
        devs = registry.list_devices()
        first_dev = devs[0] if devs else None
        return first_dev.child_id if first_dev and not is_unbound(first_dev.child_id) else "kid"

    # ── 微信小程序 / 家长端核心 API ──
    @app.get("/api/pocketclaw/mp/overview")
    async def pc_mp_overview(child_id: Optional[str] = None):
        \"\"\"微信小程序/家长端综合态势概览看板：设备状态、作业进度、古诗、口算、生词、阅读一站式获取。\"\"\"
        child = _resolve_child(child_id)
        devs = registry.list_devices()
        first_dev = devs[0] if devs else None
        tasks = [t.__dict__ for t in notes.list_tasks(child)]
        stats = notes.task_stats(child)
        is_online = bool(live)
        bat = None
        for s in live.values():
            if s.battery is not None:
                bat = s.battery
                break
        if bat is None and first_dev:
            bat = first_dev.battery

        cur_poem_title = notes.get_poem_target(child)
        cur_poem = practice.POEM_MAP.get(cur_poem_title) or practice.CORE_POEMS[0]

        reading_store = reading.ReadingStore()
        cur_reading_id = notes.get_reading_article(child)
        cur_reading = reading_store.get_article(cur_reading_id) or reading_store.articles[0]

        focus_min_today = 0
        if on_focus_today:
            try:
                focus_min_today = on_focus_today(child) or 0
            except Exception:
                pass

        return {
            "ok": True,
            "child_id": child,
            "online": is_online,
            "battery": bat,
            "device_id": first_dev.device_id if first_dev else "",
            "tasks": tasks,
            "task_stats": stats,
            "dictation_words": notes.get_dictation_words(child),
            "poem": {
                "title": cur_poem.title,
                "author": cur_poem.author,
                "dynasty": cur_poem.dynasty,
                "lines": cur_poem.lines
            },
            "arith_config": notes.get_arith_config(child),
            "vocab_count": notes.word_count(child),
            "vocab_words": [w.__dict__ for w in notes.list_words(child, limit=100)],
            "reading": {
                "id": cur_reading.id,
                "title": cur_reading.title,
                "level": cur_reading.level,
                "sentence_count": len(cur_reading.sentences),
                "sentences": cur_reading.sentences
            },
            "latest_message": notes.get_latest_parent_message(child),
            "focus_today_min": focus_min_today
        }

    # 1. 作业清单 API
    @app.get("/api/pocketclaw/mp/tasks")
    async def pc_mp_get_tasks(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "tasks": [t.__dict__ for t in notes.list_tasks(child)], "stats": notes.task_stats(child)}

    @app.post("/api/pocketclaw/mp/tasks")
    async def pc_mp_add_task(p: TaskCreatePatch):
        child = _resolve_child(p.child_id)
        t = notes.add_task(child, p.text, subject=p.subject)
        for s in list(live.values()):
            try:
                if s.mode == "task":
                    await s._show_tasks()
            except Exception:
                pass
        return {"ok": True, "task": t.__dict__, "stats": notes.task_stats(child)}

    @app.post("/api/pocketclaw/mp/tasks/{task_id}/toggle")
    async def pc_mp_toggle_task(task_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        t = notes.toggle_task(child, task_id)
        for s in list(live.values()):
            try:
                if s.mode == "task":
                    await s._show_tasks()
            except Exception:
                pass
        return {"ok": t is not None, "task": t.__dict__ if t else None, "stats": notes.task_stats(child)}

    @app.delete("/api/pocketclaw/mp/tasks/{task_id}")
    async def pc_mp_delete_task(task_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_task(child, task_id)
        for s in list(live.values()):
            try:
                if s.mode == "task":
                    await s._show_tasks()
            except Exception:
                pass
        return {"ok": ok, "stats": notes.task_stats(child)}

    @app.delete("/api/pocketclaw/mp/tasks/clear_done")
    async def pc_mp_clear_done(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        cnt = notes.clear_done_tasks(child)
        for s in list(live.values()):
            try:
                if s.mode == "task":
                    await s._show_tasks()
            except Exception:
                pass
        return {"ok": True, "cleared": cnt, "stats": notes.task_stats(child)}

    # 2. 生字听写 API
    @app.get("/api/pocketclaw/mp/dictation")
    async def pc_mp_get_dictation(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "words": notes.get_dictation_words(child)}

    @app.post("/api/pocketclaw/mp/dictation")
    async def pc_mp_set_dictation(p: DictationSetPatch):
        child = _resolve_child(p.child_id)
        words_tuple = []
        if p.raw_text:
            words_tuple = parse_dictation_words(p.raw_text)
        elif p.words:
            words_tuple = [(w, "", f"{w}的{w[0]}") for w in p.words]
        notes.set_dictation_words(child, words_tuple)
        for s in list(live.values()):
            try:
                if s.mode == "dictation":
                    await s._start_dictation(time.time())
            except Exception:
                pass
        return {"ok": True, "count": len(words_tuple), "words": words_tuple}

    # 3. 古诗背诵 API
    @app.get("/api/pocketclaw/mp/poems")
    async def pc_mp_get_poems(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        cur = notes.get_poem_target(child)
        return {
            "ok": True,
            "current_target": cur,
            "poems": [{"title": p.title, "author": p.author, "dynasty": p.dynasty, "lines": p.lines} for p in practice.CORE_POEMS]
        }

    @app.post("/api/pocketclaw/mp/poems/target")
    async def pc_mp_set_poem_target(p: PoemTargetPatch):
        child = _resolve_child(p.child_id)
        notes.set_poem_target(child, p.title)
        for s in list(live.values()):
            try:
                if s.mode == "recite":
                    await s._start_recite(p.title, time.time())
            except Exception:
                pass
        return {"ok": True, "target": p.title}

    # 4. 口算闯关配置 API
    @app.get("/api/pocketclaw/mp/arith")
    async def pc_mp_get_arith(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "config": notes.get_arith_config(child)}

    @app.post("/api/pocketclaw/mp/arith")
    async def pc_mp_set_arith(p: ArithConfigPatch):
        child = _resolve_child(p.child_id)
        cfg = {"grade": p.grade, "count": p.count, "op_type": p.op_type}
        notes.set_arith_config(child, cfg)
        return {"ok": True, "config": cfg}

    # 5. 英语生词本 API
    @app.get("/api/pocketclaw/mp/vocab")
    async def pc_mp_get_vocab(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        notes.ensure_seeded(child)
        words = notes.list_words(child, limit=200)
        return {"ok": True, "words": [w.__dict__ for w in words]}

    @app.post("/api/pocketclaw/mp/vocab")
    async def pc_mp_add_vocab(p: VocabWordPatch):
        child = _resolve_child(p.child_id)
        w = notes.add_word(child, p.word, meaning=p.meaning, example=p.example, example_cn=p.example_cn)
        return {"ok": True, "word": w.__dict__}

    @app.delete("/api/pocketclaw/mp/vocab/{word_id}")
    async def pc_mp_delete_vocab(word_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_word(child, word_id)
        return {"ok": ok}

    # 6. 英语阅读篇目 API
    @app.get("/api/pocketclaw/mp/reading")
    async def pc_mp_get_reading(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        store = reading.ReadingStore()
        cur_id = notes.get_reading_article(child)
        return {
            "ok": True,
            "current_target": cur_id,
            "articles": [{
                "id": a.id, "title": a.title, "level": a.level,
                "sentences": a.sentences
            } for a in store.articles]
        }

    @app.post("/api/pocketclaw/mp/reading/target")
    async def pc_mp_set_reading_target(p: ReadingTargetPatch):
        child = _resolve_child(p.child_id)
        notes.set_reading_article(child, p.article_id)
        for s in list(live.values()):
            try:
                if s.mode == "reading":
                    await s._start_reading(time.time())
            except Exception:
                pass
        return {"ok": True, "article_id": p.article_id}

    # 7. 亲子留言便签推屏 API
    @app.post("/api/pocketclaw/mp/message")
    async def pc_mp_send_message(p: ParentMessagePatch):
        child = _resolve_child(p.child_id)
        now = time.time()
        msg = notes.add_parent_message(child, p.text, now=now)
        ts_str = time.strftime("%H:%M", time.localtime(now))
        for s in list(live.values()):
            try:
                await s._show_parent_message(p.text, ts_str)
            except Exception as e:
                log.warning("推屏亲子留言失败: %s", e)
        return {"ok": True, "message": msg}

    # 8. 学习报告与成就勋章 API
    @app.get("/api/pocketclaw/mp/report")
    async def pc_mp_get_report(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ts = notes.task_stats(child)
        words_cnt = notes.word_count(child)
        dict_words = len(notes.get_dictation_words(child))
        focus_min_today = 0
        if on_focus_today:
            try:
                focus_min_today = on_focus_today(child) or 0
            except Exception:
                pass

        badges = [
            {"id": "b_discipline", "title": "自律小标兵", "icon": "⭐", "desc": "坚持打勾每日任务", "unlocked": ts["done"] > 0},
            {"id": "b_focus", "title": "番茄专注达人", "icon": "🔥", "desc": "完成深度学习专注", "unlocked": focus_min_today >= 25},
            {"id": "b_recite", "title": "古诗小名士", "icon": "📜", "desc": "熟练掌握课内必背古诗", "unlocked": True},
            {"id": "b_vocab", "title": "单词收藏家", "icon": "🔤", "desc": "累积英语词汇储备", "unlocked": words_cnt >= 5},
            {"id": "b_reading", "title": "阅读小先锋", "icon": "📖", "desc": "逐句跟读纯正英语", "unlocked": True},
        ]
        return {
            "ok": True,
            "child_id": child,
            "task_completion_rate": int(ts["done"] / ts["total"] * 100) if ts["total"] > 0 else 100,
            "task_stats": ts,
            "focus_minutes": focus_min_today,
            "dictation_words_count": dict_words,
            "vocab_count": words_cnt,
            "badges": badges
        }
"""

target = """        return {
            "ok": True,
            "child_id": child,
            "tasks_added": tasks_added,
            "dictation_count": dictation_count,
            "stats": notes.task_stats(child)
        }"""

if target in new_content:
    new_content = new_content.replace(target, mp_endpoints, 1)
    endpoint_path.write_text(new_content, encoding="utf-8")
    print("Successfully updated pocketclaw/endpoint.py")
else:
    print("Failed to find target return block")
    exit(1)
