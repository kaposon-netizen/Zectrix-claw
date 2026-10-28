#!/usr/bin/env python3
"""Generate illustrative Note 4 README screens from synthetic data.

Run from the repository root using server/.venv/bin/python. The screenshots
are simulations, not device captures. A CJK font can be supplied with --font.
"""
from argparse import ArgumentParser
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from PIL import Image, ImageDraw, ImageFont
from pocketclaw import display
from pocketclaw.screen_snapshot import framebuffer_png

parser = ArgumentParser()
parser.add_argument('--font', default='/System/Library/Fonts/STHeiti Medium.ttc')
parser.add_argument('--output', default=str(ROOT / 'docs/images/note4-illustrations'))
args = parser.parse_args()
out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
font = ImageFont.truetype(args.font, 24)
small = ImageFont.truetype(args.font, 18)

week = [{'courses': courses} for courses in [
    ['语文','数学','英语','体育','道德与法治','美术'],
    ['数学','语文','科学','音乐','劳动','体育'],
    ['语文','英语','数学','美术','信息科技','体育'],
    ['数学','语文','体育','英语','科学','综合实践'],
    ['语文','数学','班会','音乐','阅读','体育'],
]]
habits = [SimpleNamespace(name='每日阅读二十分钟', done=True, streak=3),
          SimpleNamespace(name='整理书包与文具', done=False, streak=0),
          SimpleNamespace(name='运动与远眺', done=False, streak=0)]
news = [{'title':'校园科学节开始啦','summary':'一起观察有趣的科学实验'},
        {'title':'周末阅读活动','summary':'分享一本喜欢的书'}]
page = {
 '01-tasks': display.render_checklist('今日作业', [('语文：朗读课文',False),('数学：完成练习',False),('英语：复习单词',True)],sel_idx=0),
 '02-dictation': display.render_dictation('生字词听写',1,8,2),
 '03-poems': display.render_recite('春晓','孟浩然','唐',['春眠不觉晓，','处处闻啼鸟。','夜来风雨声，','花落知多少。']),
 '04-arithmetic': display.render_arith('38 + 47',cur_idx=1,total=10),
 '05-english-reading': display.render_reading_article('A Sunny Day',[('It is a sunny day.','今天阳光明媚。'),('We play in the park.','我们在公园玩。')]),
 '06-focus': {'title':'专注时间','body':['','       25 : 00','','       专心学习','','   开始一段专注时光']},
 '08-feihua': display.render_feihua('春',1),
 '09-guess': display.render_guess(1,20),
 '10-fact-check': display.render_verify('企鹅是鸟类吗？','ok','企鹅属于鸟类，不会飞，但善于游泳。','查阅可靠的科普资料'),
 '11-habits': display.render_habits(habits),
 '12-timetable': display.render_timetable('周三', week[2]['courses']),
 '13-hanzi': display.render_hanzi(0,5,'春天的（  ）光',['阳','杨'],0),
 '13b-mistakes': display.render_mistakes([SimpleNamespace(category='arith', question='36 + 18', expected='54', wrong_answer='44', count=1)]),
 '14-english-words': display.render_vocab_card('apple','苹果','I eat an apple.','我吃一个苹果。',cur_idx=1,total=8),
 '15-feynman': display.render_feynman(1),
 '16-companion': display.render_chat_companion_welcome(),
 '17-family-message': display.render_parent_message('今天努力学习，做得很棒！'),
 '18-menu': display.render_menu(0),
 '19-news': display.render_news_list(news),
 '20-idiom': display.render_daily_idiom({'idiom':'闻鸡起舞','pinyin':'wen ji qi wu','meaning':'形容发奋努力，勤奋学习。'}),
}

# Reproduce the firmware's 400x300 line layout approximately. The sample font
# and pagination can differ from the display firmware; this is not a capture.
def draw_page(data):
    image = Image.new('RGB',(400,300),'white'); draw=ImageDraw.Draw(image)
    lines = [str(data.get('title') or '')] + list(data.get('body') or [])
    for i, line in enumerate(lines[:9]):
        text = str(line).replace('\x04','')
        invert = text.startswith('\x02') and '\x03' not in text
        text = text.replace('\x01','').replace('\x02','').replace('\x03','')
        y=12+i*31
        if invert: draw.rectangle((5,y,394,y+28),fill='black')
        line_font = font
        for size in range(24, 14, -1):
            candidate = ImageFont.truetype(args.font, size)
            line_font = candidate
            if draw.textlength(text, font=candidate) <= 380:
                break
        draw.text((10,y),text,font=line_font,fill='white' if invert else 'black',stroke_width=0)
    return image

for name, data in page.items():
    draw_page(data).save(out / (name+'.png'), optimize=True)

# Art bytes are the project's bundled 4bpp image and contain no family data.
from io import BytesIO
art = (ROOT / 'server/pocketclaw/card_images/starry_night.bin').read_bytes()
image = Image.open(BytesIO(framebuffer_png(art, 4))).convert('RGB')
draw = ImageDraw.Draw(image); draw.rectangle((0,269,399,299), fill='white')
draw.text((10,273),'《星月夜》  梵高',font=small,fill='black')
image.save(out / '07-gallery.png', optimize=True)
print(f'Generated {len(page)+1} example screens in {out}')
