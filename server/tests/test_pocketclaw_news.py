import pytest
import time
import asyncio
from pocketclaw.notes import NotesStore, is_news_region_relevant
from pocketclaw import display
from tests.test_pocketclaw_focus import Sink, _session

def run(coro):
    return asyncio.run(coro)

def test_news_region_relevance():
    item_national = {"title": "2026全国少儿AI挑战赛", "region": "全国"}
    item_beijing = {"title": "北京市中小学古诗风采展示", "region": "北京市"}
    item_haidian = {"title": "海淀区少儿机器人巡展", "region": "海淀区"}
    item_shanghai = {"title": "上海市青少年科博会", "region": "上海市"}
    item_guangdong = {"title": "大湾区无人机挑战赛", "region": "广东省"}

    # 1. 全国用户：全部有相关性
    assert is_news_region_relevant(item_national, "全国") is True
    assert is_news_region_relevant(item_beijing, "全国") is True
    assert is_news_region_relevant(item_shanghai, "全国") is True
    assert is_news_region_relevant(item_guangdong, "全国") is True

    # 2. 北京市用户：全国+北京+海淀相关；上海/广东无关
    assert is_news_region_relevant(item_national, "北京市") is True
    assert is_news_region_relevant(item_beijing, "北京市") is True
    assert is_news_region_relevant(item_haidian, "北京市") is True
    assert is_news_region_relevant(item_shanghai, "北京市") is False
    assert is_news_region_relevant(item_guangdong, "北京市") is False

    # 3. 海淀区用户：全国+海淀+北京相关；上海无关
    assert is_news_region_relevant(item_national, "海淀区") is True
    assert is_news_region_relevant(item_beijing, "海淀区") is True
    assert is_news_region_relevant(item_haidian, "海淀区") is True
    assert is_news_region_relevant(item_shanghai, "海淀区") is False

    # 4. 上海市用户：全国+上海相关；北京/海淀无关
    assert is_news_region_relevant(item_national, "上海市") is True
    assert is_news_region_relevant(item_shanghai, "上海市") is True
    assert is_news_region_relevant(item_beijing, "上海市") is False
    assert is_news_region_relevant(item_haidian, "上海市") is False


def test_news_filtering_blacklist_and_whitelist():
    notes = NotesStore(":memory:")
    child_id = "test_kid"

    # 默认全国
    all_news = notes.list_news(child_id, apply_filter=True)
    assert len(all_news) >= 10

    # 设为上海市：北京和江苏等非相关地区的活动被过滤
    notes.set_news_config(child_id, region="上海市")
    sh_news = notes.list_news(child_id, apply_filter=True)
    assert len(sh_news) < len(all_news)
    for it in sh_news:
        assert it["region"] in ("全国", "上海市")

    # 设置黑名单：屏蔽含有“霸王龙”或“火星”的内容
    notes.set_news_config(child_id, region="上海市", blacklist=["霸王龙", "恐龙"])
    filtered_sh = notes.list_news(child_id, apply_filter=True)
    for it in filtered_sh:
        assert "恐龙" not in it["title"] and "霸王龙" not in it["title"]

    # 设置白名单：将含有“科创”或“人工智能”的排在最前
    notes.set_news_config(child_id, region="上海市", blacklist=[], whitelist=["科创", "人工智能"])
    whitelisted_sh = notes.list_news(child_id, apply_filter=True)
    first_item = whitelisted_sh[0]
    assert first_item.get("whitelisted") is True
    assert any(k in first_item["title"] for k in ("科创", "人工智能"))


def test_news_parent_management_and_deletion():
    notes = NotesStore(":memory:")
    child_id = "test_kid"

    # 1. 家长添加信源
    src = notes.add_news_source(child_id, name="少儿机器人学会", url="https://robot.org", category="赛事", region="北京市")
    sources = notes.list_news_sources(child_id)
    assert any(s["name"] == "少儿机器人学会" for s in sources)

    # 2. 家长录入自定义活动
    item = notes.add_news_item(
        child_id,
        title="学校周末少儿创意跳蚤市场",
        summary="周六上午在学校大操场举行，学生自带手工闲置物品义卖。",
        content="全校小学生均可参加，义卖所得将捐赠希望小学，锻炼孩子沟通与财商能力。",
        category="活动",
        region="北京市",
        source="班主任通知",
    )
    assert item["title"] == "学校周末少儿创意跳蚤市场"

    # 设置地区为北京市，自定义活动在列表顶部
    notes.set_news_config(child_id, region="北京市")
    bj_items = notes.list_news(child_id, apply_filter=True)
    assert bj_items[0]["title"] == "学校周末少儿创意跳蚤市场"

    # 3. 家长删除/隐藏该活动
    notes.delete_news_item(child_id, item["id"])
    bj_items_after_del = notes.list_news(child_id, apply_filter=True)
    assert not any(it["id"] == item["id"] for it in bj_items_after_del)

    # 恢复该活动
    notes.restore_news_item(child_id, item["id"])
    bj_items_after_restore = notes.list_news(child_id, apply_filter=True)
    assert any(it["id"] == item["id"] for it in bj_items_after_restore)


def test_news_display_rendering():
    items = [
        {
            "id": f"item{i}",
            "title": f"全国少儿大赛第{i}期",
            "summary": f"面向3-6年级趣味赛项第{i}期介绍。",
            "content": f"大赛由中国科协指导，第{i}期全线上初赛。",
            "category": "赛事",
            "region": "全国",
            "source": "中国科协",
        }
        for i in range(1, 5)
    ]

    # 1. 列表渲染：单页展示 3 条消息 (page_size = 3)
    page_list = display.render_news_list(items, sel_idx=0, region="北京市")
    assert page_list["mode"] == "news"
    assert "小学新鲜事 (1/2页 · 北京市)" in page_list["title"]
    
    # 验证第一页正好包含 3 则消息（第 1, 2, 3 期，不含第 4 期）
    body_text = "\n".join(page_list["body"])
    assert "全国少儿大赛第1期" in body_text
    assert "全国少儿大赛第2期" in body_text
    assert "全国少儿大赛第3期" in body_text
    assert "全国少儿大赛第4期" not in body_text

    # 验证标题纯净展示：不添加 [赛事·全国] 等多余分类地区前缀
    for line in page_list["body"]:
        assert "[赛事·全国]" not in line
        assert "[活动·全国]" not in line

    # 验证选中项高亮反白
    assert any(line.startswith(f"{display.INVERT_MARK}> 全国少儿大赛第1期") for line in page_list["body"])

    # 验证摘要使用 16px 字体哨兵 FONT_16_MARK (\x04)
    summary_lines = [l for l in page_list["body"] if "趣味赛项" in l]
    assert len(summary_lines) >= 3
    for sl in summary_lines:
        assert sl.startswith(display.FONT_16_MARK)

    # 2. 翻页到第 4 条（索引 3）：进入第 2 页
    page_list_p2 = display.render_news_list(items, sel_idx=3, region="北京市")
    assert "小学新鲜事 (2/2页 · 北京市)" in page_list_p2["title"]
    body_p2 = "\n".join(page_list_p2["body"])
    assert "全国少儿大赛第4期" in body_p2
    assert "全国少儿大赛第1期" not in body_p2

    # 3. 详情渲染：全屏 16px 精致字号 + 结构化分条要点（简洁明了）
    page_detail = display.render_news_detail(items[0])
    assert page_detail["mode"] == "news"
    assert "详细报道" in page_detail["title"]
    assert "全国少儿大赛第1期" in "".join(page_detail["body"])
    # 每一行正文均带有 16px 哨兵
    for l in page_detail["body"]:
        assert l.startswith(display.FONT_16_MARK)
    assert any("核心看点" in l or "要点" in l for l in page_detail["body"])
    assert "长按下方按键返回主页" in page_detail["footer"]["hint"]


def test_news_long_press_back_to_list():
    """在详细报道页面长按下方按键（发送 power_long 或 down_long）必须返回新鲜事主页（列表），绝不进入 AI学伴。"""
    sink = Sink()
    sess = _session(sink)
    run(sess._start_news(0.0, speak=False))
    assert sess.mode == "news"
    assert sess._news_view == "list"

    # 进入详情页
    run(sess._news_play_or_detail(0.0))
    assert sess._news_view == "detail"

    # 1. 模拟长按下方按键（固件上报 power_long）
    run(sess.on_control({"kind": "button", "name": "power_long"}, now=0.0))
    # 必须回到新鲜事列表（主页），绝不能进入 chat (AI学伴)
    assert sess.mode == "news"
    assert sess._news_view == "list"

    # 2. 再次进入详情页，测试 down_long
    run(sess._news_play_or_detail(0.0))
    assert sess._news_view == "detail"
    run(sess.on_control({"kind": "button", "name": "down_long"}, now=0.0))
    assert sess.mode == "news"
    assert sess._news_view == "list"


@pytest.mark.anyio
async def test_news_title_expansion_and_scrolling():
    """测试标题右侧空间充分展示（扩展至30列/15汉字）与超长标题走马灯横向滚动。"""
    long_title = "中国空间站“天宫课堂”少儿科技实验方案征集活动"  # 23字 / 44列
    medium_title = "全国少儿创意编程与算法挑战赛"               # 14字 / 28列 <= 30列，无需截断
    short_title = "少儿科普展览启动"                          # 7字 / 14列

    items = [
        {"id": "1", "title": long_title, "summary": "空间站实验介绍", "category": "活动", "region": "全国"},
        {"id": "2", "title": medium_title, "summary": "编程赛项介绍", "category": "赛事", "region": "全国"},
        {"id": "3", "title": short_title, "summary": "展览介绍", "category": "展会", "region": "全国"},
    ]

    # 1. 右侧空间扩展验证：14 汉字标题不被截断，完整展示！
    page_sel_med = display.render_news_list(items, sel_idx=1, region="全国")
    body_med = "\n".join(page_sel_med["body"])
    assert medium_title in body_med, "14汉字标题应完整展示，不得截断"
    assert "…" not in [l for l in page_sel_med["body"] if medium_title in l][0]

    # 2. 超长标题初始展示（offset=0）：展示前部内容并以省略号收尾
    page_sel_long = display.render_news_list(items, sel_idx=0, region="全国", scroll_offset=0)
    body_long = "\n".join(page_sel_long["body"])
    assert "中国空间站“天宫课堂”" in body_long

    # 3. 超长标题滚动展示（offset=8）：中间平移，展示尾部内容，且尾部不加省略号
    page_scroll = display.render_news_list(items, sel_idx=0, region="全国", scroll_offset=8)
    body_scroll = "\n".join(page_scroll["body"])
    assert "课堂”少儿科技实验方案征集活动" in body_scroll

    # 4. 会话端走马灯任务调度：进入 news 且选中国空间站超长标题时自动开启滚动任务
    sink = Sink()
    sess = _session(sink)
    sess.notes.list_news = lambda *a, **k: items
    await sess._start_news(0.0, speak=False)
    assert sess._news_scroll_task is not None
    assert not sess._news_scroll_task.done()

    # 切换至第 2 项（medium_title，14汉字未超长）：无需滚动任务
    await sess._news_step(+1, 0.0)
    assert sess._news_scroll_task is None

    # 切回第 1 项（超长）：自动恢复滚动任务
    await sess._news_step(-1, 0.0)
    assert sess._news_scroll_task is not None

    # 进入详情页：任务自动停止
    await sess._news_play_or_detail(0.0)
    assert sess._news_scroll_task is None

    # 退出 news 模式：任务清理
    await sess._news_back_to_list(0.0)
    assert sess._news_scroll_task is not None
    await sess._show_menu()
    assert sess._news_scroll_task is None


def test_news_30_days_window_and_daily_update():
    """测试过去一个月（30天）严格时效过滤与每日自动滚动维护机制。"""
    notes = NotesStore(":memory:")
    child_id = "test_kid"
    now = 1792990000.0  # 基准时间戳

    # 1. 验证默认预设库资讯均在过去 30 天内，且按 days_ago 动态对齐发布日期
    all_news = notes.list_news(child_id, apply_filter=True, now=now)
    assert len(all_news) == 10
    for it in all_news:
        assert "date" in it and len(it["date"]) == 5  # "MM-DD"
        assert it["ts"] >= now - 30 * 86400, "预设资讯必须严格在过去一个月内"
        assert it["ts"] <= now

    # 2. 家长录入一条 5 天前的资讯（在 30 天内）与一条 35 天前的过往旧资讯（超过 30 天）
    item_fresh = notes.add_news_item(
        child_id,
        title="5天前近期的少儿创客挑战赛",
        summary="面向小学生开放报名",
        ts=now - 5 * 86400,
    )
    item_stale = notes.add_news_item(
        child_id,
        title="35天前早已截止的旧展会",
        summary="上个月的历史活动已结束",
        ts=now - 35 * 86400,
    )

    # 3. 严格时效过滤（apply_filter=True）：
    # item_fresh 必须展示在上屏列表；item_stale 必须被自动过滤下架！
    filtered_news = notes.list_news(child_id, apply_filter=True, now=now)
    titles = [x["title"] for x in filtered_news]
    assert "5天前近期的少儿创客挑战赛" in titles
    assert "35天前早已截止的旧展会" not in titles, "超过30天的历史活动必须被过滤"

    # 4. 家长管理端（apply_filter=False）：
    # 旧资讯可见但被标记为 expired=True
    raw_news = notes.list_news(child_id, apply_filter=False, now=now)
    stale_found = next(x for x in raw_news if x["id"] == item_stale["id"])
    assert stale_found["expired"] is True
    assert stale_found["age_days"] == 35

    fresh_found = next(x for x in raw_news if x["id"] == item_fresh["id"])
    assert fresh_found["expired"] is False
    assert fresh_found["age_days"] == 5

    # 5. 每日自动更新维护机制（check_and_update_daily_news）：
    # 自动将过期资讯从活跃池归档至 news_archived_items
    update_res = notes.check_and_update_daily_news(child_id, now=now)
    assert update_res["updated"] is True
    assert update_res["purged_count"] == 1
    assert update_res["time_window_days"] == 30

    # 6. 详情页渲染测试：属性栏完整展示发布时间（例如 时间: 10-21）
    detail_page = display.render_news_detail(item_fresh)
    detail_text = "\n".join(detail_page["body"])
    assert "时间:" in detail_text
    assert item_fresh["date"] in detail_text

