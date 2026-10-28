#include "focus_page.h"

#include <esp_log.h>
#include <esp_random.h>
#include <esp_timer.h>
#include <esp_heap_caps.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

#include "display.h"
#include "focus_digits.h"

#define TAG "focus"

namespace focus {

namespace {

// ── 一块可画点的 1bpp 位图（行优先，MSB-first，1=黑），贴屏用 Display::WriteRaw1bpp ──
class Bitmap {
 public:
    Bitmap(int w, int h) : w_(w), h_(h), stride_((w + 7) >> 3) {
        len_ = (size_t)stride_ * h_;
        buf_ = (uint8_t*)heap_caps_calloc(1, len_, MALLOC_CAP_SPIRAM);
        if (!buf_) buf_ = (uint8_t*)calloc(1, len_);     // PSRAM 不可用时退内存
    }
    ~Bitmap() { if (buf_) free(buf_); }
    bool ok() const { return buf_ != nullptr; }
    const uint8_t* data() const { return buf_; }
    size_t len() const { return len_; }
    int w() const { return w_; }
    int h() const { return h_; }

    void Dot(int x, int y) {
        if (!buf_ || x < 0 || y < 0 || x >= w_ || y >= h_) return;
        buf_[(size_t)y * stride_ + (x >> 3)] |= (uint8_t)(1 << (7 - (x & 7)));
    }
    // 粗点：以 (x,y) 为心画 r 半径的实心方块（墨水屏上比圆更清晰，也更省算）
    void Blob(int x, int y, int r) {
        for (int dy = -r; dy <= r; ++dy)
            for (int dx = -r; dx <= r; ++dx)
                if (dx * dx + dy * dy <= r * r + r) Dot(x + dx, y + dy);
    }
    void Line(int x0, int y0, int x1, int y1, int thick) {
        int dx = abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
        int dy = -abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
        int err = dx + dy;
        while (true) {
            if (thick <= 1) Dot(x0, y0); else Blob(x0, y0, thick / 2);
            if (x0 == x1 && y0 == y1) break;
            int e2 = 2 * err;
            if (e2 >= dy) { err += dy; x0 += sx; }
            if (e2 <= dx) { err += dx; y0 += sy; }
        }
    }
    void Erase(int x, int y, int r) {                 // 置白：引线上的编织纹靠挖白做出来
        if (!buf_) return;
        for (int dy = -r; dy <= r; ++dy)
            for (int dx = -r; dx <= r; ++dx) {
                const int px = x + dx, py = y + dy;
                if (px < 0 || py < 0 || px >= w_ || py >= h_) continue;
                if (dx * dx + dy * dy <= r * r + r)
                    buf_[(size_t)py * stride_ + (px >> 3)] &= (uint8_t)~(1 << (7 - (px & 7)));
            }
    }
    // 椭圆轮廓：圆柱蜡烛的顶面/底面/蜡池都靠它。half=0 全圈，1 只下半，-1 只上半
    void Ellipse(int cx, int cy, int rx, int ry, int thick, int half = 0) {
        if (rx <= 0 || ry <= 0) return;
        const int steps = 48;
        int px = 0, py = 0; bool first = true;
        for (int i = 0; i <= steps; ++i) {
            const float a = (float)i / steps * 6.28318f;
            const float sy = sinf(a);
            if (half > 0 && sy < -0.02f) { first = true; continue; }   // 只要下半（y 增大方向）
            if (half < 0 && sy > 0.02f) { first = true; continue; }
            const int x = cx + (int)lroundf(cosf(a) * rx);
            const int y = cy + (int)lroundf(sy * ry);
            if (!first) Line(px, py, x, y, thick);
            px = x; py = y; first = false;
        }
    }
    // 一段椭圆弧（角度制，0°=右、90°=下）：蜡烛纹理靠它做出长短不一的刻痕
    void Arc(int cx, int cy, int rx, int ry, int a0_deg, int a1_deg, int thick) {
        if (rx <= 0 || ry <= 0 || a1_deg <= a0_deg) return;
        const int steps = 28;
        int px = 0, py = 0;
        for (int i = 0; i <= steps; ++i) {
            const float a = (a0_deg + (a1_deg - a0_deg) * (float)i / steps) * 0.0174533f;
            const int x = cx + (int)lroundf(cosf(a) * rx);
            const int y = cy + (int)lroundf(sinf(a) * ry);
            if (i) Line(px, py, x, y, thick);
            px = x; py = y;
        }
    }
    void FillRect(int x, int y, int w, int h) {
        for (int j = 0; j < h; ++j) for (int i = 0; i < w; ++i) Dot(x + i, y + j);
    }
    void FrameRect(int x, int y, int w, int h, int thick) {
        FillRect(x, y, w, thick);
        FillRect(x, y + h - thick, w, thick);
        FillRect(x, y, thick, h);
        FillRect(x + w - thick, y, thick, h);
    }

 private:
    int w_, h_, stride_;
    size_t len_ = 0;
    uint8_t* buf_ = nullptr;
};

float frand(uint32_t& s) {                    // xorshift：可复现、不依赖 libc rand
    s ^= s << 13; s ^= s >> 17; s ^= s << 5;
    return (float)(s & 0xFFFFFF) / (float)0xFFFFFF;
}

}  // namespace

void Fuse::Clear() { xs.clear(); ys.clear(); cum.clear(); }

void GenerateFuse(Fuse& out, uint32_t seed) {
    out.Clear();
    // 混进时间戳：esp_random 在 RF 起来之前是伪随机，开机就开一轮时容易撞上同一串
    uint32_t s = seed ? seed : (esp_random() ^ (uint32_t)esp_timer_get_time());
    if (s == 0) s = 0x9E3779B9;

    // ── 骨架：随机蛇形 ──────────────────────────────────────────────────────
    // 第一版是「固定从右下走到左上的两条正弦叠加」，参数再随机也还是同一副长相
    // （用户 2026-09-12：「不要用同一根引线的设计跑所有的番茄钟」）。
    // 改成先掷骨架再平滑：主方向（竖/横）、折返层数、每层落点、走向，全都重掷 →
    // 每轮的引线在**结构上**就不一样，而不只是抖动幅度不同。
    const bool vertical = frand(s) < 0.58f;          // 竖着蛇行 / 横着蛇行
    const int bands = 6 + (int)(frand(s) * 4.0f);    // 6–9 个折返层（层多 = 引线长）
    const bool reverse = frand(s) < 0.5f;            // 从哪头起烧
    const float m = 16.f;                            // 边距：星芒要有地方伸展

    std::vector<float> cx, cy;
    for (int i = 0; i < bands; ++i) {
        const float u = (bands > 1) ? (float)i / (float)(bands - 1) : 0.f;   // 沿主方向推进
        // 交替贴两侧，但每次贴多深是随机的 → 有的层几乎横穿，有的层只探一点点
        const float side = (i % 2 == 0) ? (0.03f + frand(s) * 0.20f)
                                        : (0.77f + frand(s) * 0.20f);   // 贴得越两侧，单段越长
        const float jitter = (frand(s) - 0.5f) * 0.10f;                      // 主方向上的抖动
        const float uu = u + ((i == 0 || i == bands - 1) ? 0.f : jitter);     // 端点不抖，免得越界
        if (vertical) {
            cx.push_back(m + side * (kFuseW - 2 * m));
            cy.push_back(m + uu * (kFuseH - 2 * m));
        } else {
            cx.push_back(m + uu * (kFuseW - 2 * m));
            cy.push_back(m + side * (kFuseH - 2 * m));
        }
    }
    if (reverse) {
        std::reverse(cx.begin(), cx.end());
        std::reverse(cy.begin(), cy.end());
    }

    // ── Catmull-Rom 过所有骨架点：保证经过每个折返点，且处处平滑（不折尖角）──
    const int per_seg = 70;
    const int n = (int)cx.size();
    for (int i = 0; i + 1 < n; ++i) {
        const int i0 = (i - 1 < 0) ? 0 : i - 1;
        const int i2 = i + 1;
        const int i3 = (i + 2 >= n) ? n - 1 : i + 2;
        for (int k = 0; k < per_seg; ++k) {
            const float t = (float)k / (float)per_seg;
            const float t2 = t * t, t3 = t2 * t;
            const float bx = 0.5f * ((2.f * cx[i]) + (-cx[i0] + cx[i2]) * t
                + (2.f * cx[i0] - 5.f * cx[i] + 4.f * cx[i2] - cx[i3]) * t2
                + (-cx[i0] + 3.f * cx[i] - 3.f * cx[i2] + cx[i3]) * t3);
            const float by = 0.5f * ((2.f * cy[i]) + (-cy[i0] + cy[i2]) * t
                + (2.f * cy[i0] - 5.f * cy[i] + 4.f * cy[i2] - cy[i3]) * t2
                + (-cy[i0] + 3.f * cy[i] - 3.f * cy[i2] + cy[i3]) * t3);
            int px = (int)lroundf(bx), py = (int)lroundf(by);
            if (px < 3) px = 3;
            if (px > kFuseW - 4) px = kFuseW - 4;
            if (py < 3) py = 3;
            if (py > kFuseH - 4) py = kFuseH - 4;
            if (!out.xs.empty() && out.xs.back() == (int16_t)px && out.ys.back() == (int16_t)py)
                continue;                                  // 去掉重复点，弧长表才准
            out.xs.push_back((int16_t)px);
            out.ys.push_back((int16_t)py);
        }
    }
    out.xs.push_back((int16_t)cx[n - 1]);
    out.ys.push_back((int16_t)cy[n - 1]);

    out.cum.assign(out.xs.size(), 0.f);
    for (size_t i = 1; i < out.xs.size(); ++i) {
        const float ddx = out.xs[i] - out.xs[i - 1], ddy = out.ys[i] - out.ys[i - 1];
        out.cum[i] = out.cum[i - 1] + sqrtf(ddx * ddx + ddy * ddy);
    }
    ESP_LOGI(TAG, "生成引线：%s %d 层 %s，%d 点，全长 %.0fpx，seed=%u",
             vertical ? "竖蛇形" : "横蛇形", bands, reverse ? "反向" : "正向",
             (int)out.xs.size(), out.cum.empty() ? 0.f : out.cum.back(), (unsigned)s);
}

void BurnPoint(const Fuse& f, float t, int& x, int& y, size_t& seg) {
    seg = 0; x = y = 0;
    if (f.Empty()) return;
    if (t < 0) t = 0;
    if (t > 1) t = 1;
    const float total = f.cum.back();
    if (!(total > 0)) { x = f.xs[0]; y = f.ys[0]; return; }
    const float d = t * total;
    size_t lo = 0, hi = f.cum.size() - 1;                 // 二分：长引线也不逐点扫
    while (lo < hi) { size_t mid = (lo + hi) / 2; if (f.cum[mid] < d) lo = mid + 1; else hi = mid; }
    seg = lo > 0 ? lo : 1;
    const float segLen = f.cum[seg] - f.cum[seg - 1];
    const float r = segLen > 0 ? (d - f.cum[seg - 1]) / segLen : 0.f;
    x = (int)lroundf(f.xs[seg - 1] + (f.xs[seg] - f.xs[seg - 1]) * r);
    y = (int)lroundf(f.ys[seg - 1] + (f.ys[seg] - f.ys[seg - 1]) * r);
}

namespace {

// 引线区：未烧的部分画实线（带内部白色虚线＝引线的编织纹），烧过的画稀疏灰点（余烬），
// 燃点画星芒 + 迸溅火星。全部只有黑白两色，墨水屏上对比最清楚。
// ox/oy：位图左上角在引线区里的偏移（整屏帧为 0；局部帧为燃点方块的左上角）
void DrawFuse(Bitmap& bm, const Fuse& f, float prog, bool burning, uint32_t& rs,
              int ox = 0, int oy = 0) {
    if (f.Empty()) return;
    int bx = 0, by = 0; size_t seg = 0;
    BurnPoint(f, prog, bx, by, seg);
    bx -= ox; by -= oy;

    // ① 未烧段：粗实线
    for (size_t i = seg; i + 1 < f.xs.size(); ++i)
        bm.Line(f.xs[i] - ox, f.ys[i] - oy, f.xs[i + 1] - ox, f.ys[i + 1] - oy, 7);
    if (seg < f.xs.size()) bm.Line(bx, by, f.xs[seg] - ox, f.ys[seg] - oy, 7);
    // ② 编织纹：在粗线中心每隔一小段挖一个白点，做出参考稿那种「一节一节」的引线质感。
    //    **锚点必须是固定索引**（i % 8 == 4），不能写成 seg+4——那样白点会跟着燃点整体平移，
    //    看上去「还没烧到的线也在动」（第一版就是这个毛病）。
    for (size_t i = seg; i + 1 < f.xs.size(); ++i)
        if (i % 8 == 4) bm.Erase(f.xs[i] - ox, f.ys[i] - oy, 1);
    // ③ 已烧段：稀疏圆点（余烬）——和未烧的粗实线要一眼可辨，不然读不出「烧到哪了」。
    //    间隔按弧长换算：引线长短不同，但余烬点的疏密看起来得一样。
    const float dot_gap = 7.f;
    float next_d = 0.f;
    for (size_t i = 0; i + 1 < seg; ++i) {
        if (f.cum[i] >= next_d) { bm.Blob(f.xs[i] - ox, f.ys[i] - oy, 1); next_d = f.cum[i] + dot_gap; }
    }

    if (!burning) return;
    // ④ 燃点：实心核 + 10 道长星芒（参考稿的火花很显眼，小了就淹在引线里）
    bm.Blob(bx, by, 5);
    for (int k = 0; k < 10; ++k) {
        const float a = k * 0.628f + frand(rs) * 0.22f;
        const int r0 = 8, r1 = 18 + (int)(frand(rs) * 12);
        bm.Line(bx + (int)(cosf(a) * r0), by + (int)(sinf(a) * r0),
                bx + (int)(cosf(a) * r1), by + (int)(sinf(a) * r1), 3);
    }
    // ⑤ 迸溅火星：朝上扇形扬起，近密远疏
    for (int k = 0; k < 18; ++k) {
        const float a = -2.2f + frand(rs) * 2.6f;
        const float r = 16.f + frand(rs) * 34.f;
        bm.Blob(bx + (int)(cosf(a) * r), by + (int)(sinf(a) * r), frand(rs) > 0.55f ? 1 : 0);
    }
}

// ── 蜡烛 ───────────────────────────────────────────────────────────────────
// 烛身越烧越短（高度按剩余比例），顶端火苗每帧重掷（摇曳）。ox/oy 同 DrawFuse：局部帧的偏移。
int CandleBodyTop(float prog) {
    const int body_h = (int)((1.f - (prog < 0 ? 0 : (prog > 1 ? 1 : prog))) * kCandleMaxH);
    return kCandleBaseY - kCandleStandH - body_h;
}

void DrawCandle(Bitmap& bm, float prog, bool burning, uint32_t& rs, int ox = 0, int oy = 0) {
    if (prog < 0) prog = 0;
    if (prog > 1) prog = 1;
    const int cx = kFuseW / 2 - ox;
    const int body_bottom = kCandleBaseY - oy;          // 直接落在地面：烛台已去掉（用户 2026-09-12）
    const int body_top = CandleBodyTop(prog) - oy;
    const int hw = kCandleBodyW / 2;
    const int ry = 9;                                    // 圆柱顶面/底面椭圆的短半轴

    const bool has_body = (body_bottom - body_top) > 8;
    if (has_body) {
        // ① 圆柱：两条侧线 + 底面下半弧 + 顶面整圈椭圆（有顶面才像圆柱，不是一块木板）
        bm.Line(cx - hw, body_top, cx - hw, body_bottom, 3);
        bm.Line(cx + hw, body_top, cx + hw, body_bottom, 3);
        bm.Ellipse(cx, body_bottom, hw, ry, 3, +1);
        bm.Ellipse(cx, body_top, hw, ry, 3, 0);
        // ② 蜡池：顶面里再套一圈小椭圆，中间是化开的蜡
        bm.Ellipse(cx, body_top + 2, hw - 11, ry - 4, 2, 0);
        // ③ 蜡纹：圆柱面上的横向刻痕。
        //    **锚在烛身底部**（距底多少像素是写死的），所以蜡烛烧短时上面的纹理逐个消失、
        //    下面的纹身纹丝不动——按顶部算的话，纹理会跟着顶面一路往下挪，屏上看着一直在动
        //    （第一版的毛病，用户 2026-09-12 报修）。
        //    长度也各不相同：整圈弧太规整，像车床车出来的螺纹。角度写死 → 不随帧变。
        static const struct { int up, a0, a1; } kVeins[] = {
            {20, 14, 166}, {36, 62, 142}, {55, 10, 104}, {74, 78, 170},
            {92, 20, 160}, {110, 52, 120}, {128, 18, 128},
        };
        for (const auto& v : kVeins) {
            const int yy = body_bottom - v.up;
            if (yy > body_top + 15 && yy < body_bottom - 8)
                bm.Arc(cx, yy, hw - 3, ry - 3, v.a0, v.a1, 1);
        }
        // ④ 蜡油：从蜡池边缘**溢出**顺身流下，三道不等长，末端挂一滴
        const int drips[3][2] = {{-hw + 9, 0}, {hw - 10, 8}, {-hw + 30, 18}};
        for (int i = 0; i < 3; ++i) {
            const int dx = cx + drips[i][0];
            const int dl = 9 + (int)(prog * 46.f) + drips[i][1];
            const int dy2 = body_top + dl;
            if (dy2 < body_bottom - 6) {
                bm.Line(dx, body_top + ry - 4, dx, dy2, 4);
                bm.Blob(dx, dy2 + 1, 2);
            }
        }
    }

    if (!burning || !has_body) return;

    // ⑤ 灯芯 + 火苗：外焰轮廓 + 内焰留白 + 焰心，每帧重掷 → 在摇
    // 整体微移：火苗每帧在 1–2 像素内挪一下，黑像素不总压同一处，残影不易烙深
    const int drift = (int)(frand(rs) * 3.f) - 1;
    const int wick_top = body_top - 4 + drift;
    bm.Line(cx, body_top - 1, cx, wick_top - 3, 3);
    // 摆动方向：xorshift 刚播种时低位偏，第一发 frand 会一直往同一边倒——先空转两次再取。
    frand(rs); frand(rs);
    const float lean = (frand(rs) * 2.f - 1.f) * 6.f;           // 左右都摆，幅度 ±6
    const int fh = 28 + (int)(frand(rs) * 8.f);                 // 火苗收小（原来 40–52 太大）
    const int fw = 11 + (int)(frand(rs) * 3.f);
    const int tipx = cx + (int)(lean * 1.7f), tipy = wick_top - fh;
    for (int side = -1; side <= 1; side += 2) {
        int px = cx + side * fw, py = wick_top - 2;
        const int steps = 14;
        for (int k = 1; k <= steps; ++k) {
            const float t = (float)k / steps;
            const float w = fw * sqrtf(1.f - t) * (1.f - 0.35f * t * t * t) * 1.12f;
            const int nx = (int)(cx + side * w + lean * t * 1.7f);
            const int ny = (int)(wick_top - 2 - t * (fh - 2));
            bm.Line(px, py, nx, ny, 2);
            px = nx; py = ny;
        }
        bm.Line(px, py, tipx, tipy, 2);
    }
    bm.Line(cx - fw, wick_top - 2, cx + fw, wick_top - 2, 2);
    // 内焰：挖一个上尖下圆的白块，留下一圈外焰。
    // **只挖到火苗腰部为止**——挖到顶尖会把外焰轮廓削掉，火苗就不尖了（第一版的毛病）。
    for (int k = 0; k < 5; ++k) {
        const float t = (float)k / 5.f;
        const int r = (int)((fw - 6) * (1.f - t * 0.75f));
        bm.Erase(cx + (int)(lean * (0.4f + t * 0.7f)),
                 wick_top - 9 - (int)(t * fh * 0.5f), r > 1 ? r : 1);
    }
    bm.Blob(cx + (int)(lean * 0.35f), wick_top - 6, 2);         // 焰心：芯上那一点亮
    // ⑥ 光晕：火苗两侧的放射短线，长度每帧微变 → 一闪一闪
    for (int k = 0; k < 6; ++k) {
        const float a = (k < 3 ? 3.14159f : 0.f) + 0.45f + (k % 3) * 0.55f + (frand(rs) - 0.5f) * 0.2f;
        const int y0 = wick_top - fh / 2;
        const int r0 = fw + 6, r1 = r0 + 5 + (int)(frand(rs) * 7.f);
        bm.Line(cx + (int)(cosf(a) * r0), y0 + (int)(sinf(a) * r0 * 0.5f),
                cx + (int)(cosf(a) * r1), y0 + (int)(sinf(a) * r1 * 0.5f), 2);
    }
}

// 蜡烛烧完：只剩一小摊残蜡 + 一缕烟（烛台已取消）
void DrawCandleOut(Bitmap& bm, uint32_t& rs) {
    (void)rs;
    const int cx = kFuseW / 2;
    const int base = kCandleBaseY;
    const int hw = kCandleBodyW / 2;
    // 残蜡：一摊压扁的椭圆，边缘被蜡油拖出两个角
    bm.Ellipse(cx, base - 6, hw, 11, 3, 0);
    bm.Ellipse(cx, base - 6, hw - 12, 6, 2, 0);
    bm.Blob(cx - hw - 5, base - 3, 3);
    bm.Blob(cx + hw + 4, base - 2, 2);
    for (int k = 0; k < 11; ++k) {                  // 烟：往上飘，越高越散
        const int y = base - 24 - k * 10;
        const int x = cx + (int)(sinf(k * 0.85f) * (5 + k * 1.4f));
        bm.Blob(x, y, k < 4 ? 2 : (k < 8 ? 1 : 0));
    }
}

// 绽放：以引线末端为心，放射状长星芒 + 外圈火星
void DrawBloom(Bitmap& bm, const Fuse& f, uint32_t& rs) {
    if (f.Empty()) return;
    const int cx = f.xs.back(), cy = f.ys.back();
    {   // 整条已成灰烬：同样按弧长等距打点
        float next_d = 0.f;
        for (size_t i = 0; i + 1 < f.xs.size(); ++i)
            if (f.cum[i] >= next_d) { bm.Blob(f.xs[i], f.ys[i], 1); next_d = f.cum[i] + 7.f; }
    }
    bm.Blob(cx, cy, 5);
    for (int k = 0; k < 18; ++k) {
        const float a = k * 0.349f + frand(rs) * 0.12f;
        const int r1 = 20 + (int)(frand(rs) * 18);
        bm.Line(cx + (int)(cosf(a) * 8), cy + (int)(sinf(a) * 8),
                cx + (int)(cosf(a) * r1), cy + (int)(sinf(a) * r1), 2);
        bm.Blob(cx + (int)(cosf(a) * (r1 + 6)), cy + (int)(sinf(a) * (r1 + 6)), 1);
    }
}

// 大号数字：把 focus_digits.h 的字模逐字形贴到屏上（字模 1=黑，正是 WriteRaw1bpp 的约定）
void DrawBigNumber(Display* disp, int value, int x, int y) {
    char buf[8];
    snprintf(buf, sizeof(buf), "%d", value < 0 ? 0 : (value > 999 ? 999 : value));
    int total_w = 0;
    for (const char* p = buf; *p; ++p) {
        const int g = *p - '0';
        if (g >= 0 && g <= 9) total_w += FOCUS_DIGIT_W[g] + 8;
    }
    if (total_w > 0) total_w -= 8;
    // 在右侧信息区 [222, 390] (宽 168px) 内水平居中对齐
    int cx = 222 + (168 - total_w) / 2;
    if (cx < 222) cx = x;
    for (const char* p = buf; *p; ++p) {
        const int g = *p - '0';
        if (g < 0 || g > 9) continue;
        const int w = FOCUS_DIGIT_W[g];
        disp->WriteRaw1bpp(cx, y, w, FOCUS_DIGIT_H,
                           FOCUS_DIGIT_BITS + FOCUS_DIGIT_OFF[g],
                           (size_t)((w + 7) / 8) * FOCUS_DIGIT_H);
        cx += w + 8;
    }
}

}  // namespace

void RenderAnimFrame(Display* disp, const Fuse& fuse, Style style, float progress) {
    if (!disp) return;
    if (style == Style::Candle) {
        // 火苗那一块：位置随烛身缩短连续下移；框够大，罩得住摇曳幅度与光点
        const int cx = kFuseW / 2, top = CandleBodyTop(progress);
        int ox = cx - kFlameBoxW / 2, oy = top - kFlameBoxH + 28;
        if (ox < 0) ox = 0;
        if (oy < 0) oy = 0;
        if (ox > kFuseW - kFlameBoxW) ox = kFuseW - kFlameBoxW;
        if (oy > kFuseH - kFlameBoxH) oy = kFuseH - kFlameBoxH;
        if (ox < 0) ox = 0;
        if (oy < 0) oy = 0;
        uint32_t rs = esp_random() | 1u;
        Bitmap bm(kFlameBoxW, kFlameBoxH);
        if (!bm.ok()) return;
        DrawCandle(bm, progress, true, rs, ox, oy);
        disp->WriteRaw1bpp(kFuseX + ox, kFuseY + oy, bm.w(), bm.h(), bm.data(), bm.len());
        disp->RequestUrgentRefresh();
        return;
    }
    if (fuse.Empty()) return;
    int bx = 0, by = 0; size_t seg = 0;
    BurnPoint(fuse, progress, bx, by, seg);

    // 燃点方块：星芒最长 30、火星最远 50 → 56 的半径刚好罩住，且燃点每帧只挪不到 1px，
    // 上一帧的火星一定落在同一块里，会被这块的重绘覆盖掉，不会留下残迹。
    const int half = kSparkBox / 2;
    int ox = bx - half, oy = by - half;
    if (ox < 0) ox = 0;
    if (oy < 0) oy = 0;
    if (ox > kFuseW - kSparkBox) ox = kFuseW - kSparkBox;
    if (oy > kFuseH - kSparkBox) oy = kFuseH - kSparkBox;
    if (ox < 0) ox = 0;
    if (oy < 0) oy = 0;
    const int w = kSparkBox < kFuseW ? kSparkBox : kFuseW;
    const int h = kSparkBox < kFuseH ? kSparkBox : kFuseH;

    uint32_t rs = esp_random() | 1u;
    Bitmap bm(w, h);
    if (!bm.ok()) return;
    DrawFuse(bm, fuse, progress, true, rs, ox, oy);   // 这块里的引线照原样重画，燃点重掷
    disp->WriteRaw1bpp(kFuseX + ox, kFuseY + oy, w, h, bm.data(), bm.len());
    disp->RequestUrgentRefresh();     // 局部刷新：只推燃点这一小块
}

void RenderScreen(Display* disp, const Fuse& fuse, Style style, int left_s, int total_s,
                  bool paused, int today_min, bool done) {
    if (!disp) return;
    uint32_t rs = esp_random() | 1u;

    const int left_min = (left_s + 59) / 60;              // 剩余分钟（向上取整：还有 1 秒也显示 1）
    const float prog = total_s > 0 ? 1.f - (float)left_s / (float)total_s : 1.f;

    // ── ① 文字层（DrawTexts 带 clear=整屏白底）──
    std::vector<Display::TextItem> items;
    const bool candle = (style == Style::Candle);
    items.push_back({done ? std::string(candle ? "蜡烛燃尽" : "花火绽放")
                          : (paused ? std::string("已暂停") : std::string("专注中")),
                     10, 8, 24, false});
    items.push_back({candle ? "蜡烛模式" : "火花模式", 324, 12, 16, false});
    if (done) {
        items.push_back({"这一轮烧完啦", 234, 96, 24, false});
        items.push_back({"站起来动一动", 234, 132, 24, false});
    } else {
        items.push_back({paused ? "分钟·说继续烧"
                                : (candle ? "分钟后蜡烛烧完" : "分钟后引线烧完"), 222, 178, 24, false});
    }
    char foot[64];
    snprintf(foot, sizeof(foot), "今日已专注 %d 分钟", today_min < 0 ? 0 : today_min);
    items.push_back({foot, 10, 270, 16, false});
    items.push_back({done ? "说“番茄钟”再来一轮" : "上键暂停 · 或直接说话", 222, 270, 16, false});
    disp->DrawTexts(items, true);

    // ── ② 图形层（不与文字区重叠，逐块贴；WriteRaw1bpp 不清屏）──
    {
        Bitmap bm(kFuseW, kFuseH);
        if (bm.ok()) {
            if (candle) {
                if (done) DrawCandleOut(bm, rs);
                else DrawCandle(bm, prog, !paused, rs);
            } else {
                if (done) DrawBloom(bm, fuse, rs);
                else DrawFuse(bm, fuse, prog, !paused, rs);
            }
            disp->WriteRaw1bpp(kFuseX, kFuseY, bm.w(), bm.h(), bm.data(), bm.len());
        }
    }
    {
        Bitmap bar(kBarW, kBarH);
        if (bar.ok()) {
            bar.FrameRect(0, 0, kBarW, kBarH, 2);
            const int filled = (int)((kBarW - 6) * (prog < 0 ? 0 : (prog > 1 ? 1 : prog)));
            if (filled > 0) bar.FillRect(3, 3, filled, kBarH - 6);
            disp->WriteRaw1bpp(kBarX, kBarY, bar.w(), bar.h(), bar.data(), bar.len());
        }
    }
    if (!done) DrawBigNumber(disp, left_min, kNumX, kNumY);

    // 顶部分隔线（参考稿有一条）：单独一块细位图，免得和标题文字抢区
    {
        Bitmap rule(380, 3);
        if (rule.ok()) { rule.FillRect(0, 0, 380, 3); disp->WriteRaw1bpp(10, 38, 380, 3, rule.data(), rule.len()); }
    }

    disp->RequestUrgentFullRefresh();
    ESP_LOGI(TAG, "%s屏：剩 %ds/%ds（%d 分）prog=%.2f paused=%d done=%d",
             candle ? "蜡烛" : "花火", left_s, total_s, left_min, prog, (int)paused, (int)done);
}

}  // namespace focus
