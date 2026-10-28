#include "audio/wake_endpoint.h"
#include <cassert>
#include <cstdint>
int main() {
    int16_t silence[4] = {0, 0, 0, 0};
    int16_t voice[4] = {900, -900, 900, -900};
    WakeEndpoint endpoint;
    endpoint.Start(100);
    assert(endpoint.Feed(silence, 4, 5099) == WakeEndpoint::Result::Continue);
    assert(endpoint.Feed(silence, 4, 5100) == WakeEndpoint::Result::NoSpeech);
    endpoint.Start(10000);
    assert(endpoint.Feed(voice, 4, 10500) == WakeEndpoint::Result::Continue);
    assert(endpoint.Feed(silence, 4, 11699) == WakeEndpoint::Result::Continue);
    assert(endpoint.Feed(silence, 4, 11700) == WakeEndpoint::Result::Complete);
    endpoint.Start(20000);
    assert(endpoint.Feed(voice, 4, 40000) == WakeEndpoint::Result::Complete);
}
