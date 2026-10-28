// 花火番茄钟：设备本地渲染（引线 + 大号数字 + 进度条）。
//
// 为什么放本地：墨水屏一次全刷几百毫秒，引线燃点与分钟数要跟着秒走——放服务端就得每分钟推一屏，
// 断网/服务端重启即停摆。改成服务端只发「开始/停止」，设备自己跑秒、自己画。
//
// 引线形状**每次开始时本地随机生成**（esp_random 播种），所以每一轮花火都长得不一样。
#ifndef _FOCUS_PAGE_H_
#define _FOCUS_PAGE_H_

#include <stdint.h>

#include <string>
#include <vector>

class Display;

namespace focus {

// 400×300 墨水屏上的版式分区（与 focus_page.cc 的绘制一一对应）
constexpr int kScreenW = 400;
constexpr int kScreenH = 300;
constexpr int kFuseX = 6, kFuseY = 44, kFuseW = 214, kFuseH = 190;      // 引线区（左）
constexpr int kBarX = 8, kBarY = 246, kBarW = 384, kBarH = 14;           // 进度条（底）
constexpr int kNumX = 228, kNumY = 62;                                   // 大号数字左上角（右）
constexpr int kSparkBox = 120;          // 动画帧只刷燃点周围这么大一块（罩得住星芒与火星）

// 外观：火花引线（默认）/ 蜡烛。孩子不明确说「蜡烛模式」就一直是火花。
enum class Style { Spark, Candle };

// 蜡烛版式（都在引线区坐标系内）
constexpr int kCandleBaseY = kFuseH - 12;   // 烛身落地的位置（烛台已取消）
constexpr int kCandleStandH = 0;            // 烛台高：已去掉，保留常量避免改动面扩散
constexpr int kCandleBodyW = 62;            // 烛身宽
constexpr int kCandleMaxH = 138;            // 烛身满高（剩余比例乘它）
constexpr int kFlameBoxW = 96, kFlameBoxH = 84;     // 动画帧只刷火苗这一块（罩住摆动与光晕）
// 蜡烛的火苗**原地**闪，同一片像素反复黑白翻转 → 墨水屏残影会就地累积
// （火花没这个问题：燃点一直在往前走，残影被走过的新内容覆盖掉了）。
// 所以蜡烛降帧：2.5 秒一摇，本来也更像蜡烛该有的静。
constexpr int kCandleFrameMs = 2500;

// 一条引线：屏幕坐标点列（相对引线区左上角）。点间距约 2–4px，够密才能按长度均匀推进燃点。
struct Fuse {
    std::vector<int16_t> xs, ys;
    std::vector<float> cum;      // 累计弧长，cum.back() = 全长
    void Clear();
    bool Empty() const { return xs.size() < 2; }
};

// 随机生成一条引线（正弦叠加 + 随机相位/振幅，从右下走到左上，末端留出花火位置）。
// seed=0 表示用 esp_random()。
void GenerateFuse(Fuse& out, uint32_t seed);

// 把 0~1 进度换算成引线上的插值点（按弧长，不按点序号——点疏密不均时燃点才不会忽快忽慢）。
void BurnPoint(const Fuse& f, float t, int& x, int& y, size_t& seg);

// 画一整屏。left_s=剩余秒，total_s=总秒，paused=暂停态，today_min=今日已专注分钟。
// done=true 时画「花火绽放」收尾屏。
void RenderScreen(Display* disp, const Fuse& fuse, Style style, int left_s, int total_s,
                  bool paused, int today_min, bool done);

// 动画帧：**只重绘会动的那一小块**并局部刷新。
// 火花：引线本身（粗线、编织纹白点、余烬点）位置全固定，只有燃点的星芒与火星在变；
// 蜡烛：烛身/烛台位置只随分钟变，只有顶端火苗在摇。
// 所以刷新区域能缩到方寸之内，墨水屏刷得更快、残影也更轻。
void RenderAnimFrame(Display* disp, const Fuse& fuse, Style style, float progress);

}  // namespace focus

#endif  // _FOCUS_PAGE_H_
