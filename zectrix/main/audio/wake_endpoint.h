#pragma once
#include <cstddef>
#include <cstdint>
#include <cstdlib>

// Endpoint an utterance after a locally detected wake word. Times are monotonic milliseconds.
class WakeEndpoint {
public:
    void Start(int64_t now_ms) { started_ = last_voice_ = now_ms; heard_ = false; }
    enum class Result { Continue, Complete, NoSpeech };
    Result Feed(const int16_t* samples, size_t count, int64_t now_ms) {
        int64_t magnitude = 0;
        for (size_t i = 0; i < count; ++i) magnitude += std::abs(static_cast<int>(samples[i]));
        if (count && magnitude / static_cast<int64_t>(count) >= 500) {
            heard_ = true;
            last_voice_ = now_ms;
        }
        if (now_ms - started_ >= 20000) return heard_ ? Result::Complete : Result::NoSpeech;
        if (!heard_ && now_ms - started_ >= 5000) return Result::NoSpeech;
        if (heard_ && now_ms - last_voice_ >= 1200) return Result::Complete;
        return Result::Continue;
    }
private:
    int64_t started_ = 0, last_voice_ = 0;
    bool heard_ = false;
};
