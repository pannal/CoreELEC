#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "orender.h"

typedef struct
{
  int tail_pending;
  int drained;
  int source_present;
} FakeRenderer;

uint32_t orender_version_major(void)
{
  return ORENDER_ABI_MAJOR;
}

uint32_t orender_version_minor(void)
{
  return ORENDER_ABI_MINOR;
}

OrenderRenderer* orender_create(const OrenderConfig* config)
{
  (void)config;
  FakeRenderer* r = (FakeRenderer*)calloc(1, sizeof(*r));
  if (r)
  {
    /* Two packets held, as on the decode thread; drain returns one per call. */
    r->tail_pending = 2;
    r->source_present = 1;
  }
  return (OrenderRenderer*)r;
}

void orender_destroy(OrenderRenderer* renderer)
{
  free(renderer);
}

void orender_reset(OrenderRenderer* renderer)
{
  FakeRenderer* r = (FakeRenderer*)renderer;
  r->tail_pending = 0;
  r->drained = 0;
  r->source_present = 0;
}

int orender_process(OrenderRenderer* renderer,
                    const uint8_t* packet,
                    uintptr_t packet_len,
                    int64_t pts_us,
                    float* out,
                    uintptr_t out_cap_samples,
                    uintptr_t* out_frames,
                    uint32_t* out_channels,
                    int64_t* out_pts_us)
{
  FakeRenderer* r = (FakeRenderer*)renderer;
  (void)packet;
  (void)packet_len;
  (void)pts_us;
  *out_frames = 0;
  *out_channels = 2;
  *out_pts_us = 2000;

  /* The first FEED deliberately produces nothing: the only first-frame data
   * must come from FLUSH. A FEED after draining proves the engine stays usable
   * and clears the source label so the helper must emit an empty value. */
  if (!r->drained)
    return 0;
  if (out_cap_samples < 2)
    return 1;
  r->source_present = 0;
  out[0] = 0.25f;
  out[1] = -0.25f;
  *out_frames = 1;
  return 0;
}

int orender_object_count(const OrenderRenderer* renderer)
{
  (void)renderer;
  return 0;
}

int orender_has_objects(const OrenderRenderer* renderer)
{
  (void)renderer;
  return 1;
}

uint32_t orender_bed_layout(const OrenderRenderer* renderer, uint8_t* out, uint32_t cap)
{
  static const uint8_t height_labels[] = {
      OrenderChannelLabel_Lh,
      OrenderChannelLabel_Rh,
      OrenderChannelLabel_Ch,
      OrenderChannelLabel_Lhs,
      OrenderChannelLabel_Rhs,
  };
  static const uint8_t floor_labels[] = {
      OrenderChannelLabel_L,
      OrenderChannelLabel_R,
      OrenderChannelLabel_C,
      OrenderChannelLabel_Ls,
      OrenderChannelLabel_Rs,
  };
  const FakeRenderer* r = (const FakeRenderer*)renderer;
  const uint8_t* labels = r->source_present ? height_labels : floor_labels;
  const uint32_t n = sizeof(height_labels);
  if (out && cap >= n)
    memcpy(out, labels, n);
  return n;
}

uint32_t orender_decoded_sample_rate(const OrenderRenderer* renderer)
{
  (void)renderer;
  return 48000;
}

uint32_t orender_source_label(const OrenderRenderer* renderer, char* out, uint32_t cap)
{
  static const char label[] = "DTS-HD HRA + DTS:X 7.1.4";
  const FakeRenderer* r = (const FakeRenderer*)renderer;
  const uint32_t n = (uint32_t)(sizeof(label) - 1);
  if (!r->source_present)
    return 0;
  if (out && cap > n)
    memcpy(out, label, sizeof(label));
  return n;
}

/* The HRIR set in use: the engine starts on its embedded KEMAR set and
 * swaps a configured SOFA set in once built, so the first report here says
 * "saf" and a later one "sofa" - the change the helper must pass on. */
uint32_t orender_hrir_in_use(const OrenderRenderer* renderer, char* out, uint32_t cap)
{
  const FakeRenderer* r = (const FakeRenderer*)renderer;
  const char* name = r->source_present ? "saf" : "sofa";
  const uint32_t n = (uint32_t)strlen(name);
  if (out && cap > n)
    memcpy(out, name, n + 1);
  return n;
}

/* Knows the one option the helper sets, so the default it picks per codec
 * shows on the open line. */
int orender_set_option(OrenderRenderer* renderer, const char* key, const char* value)
{
  (void)renderer;
  if (strcmp(key, "decode_thread") != 0)
    return -1;
  return strcmp(value, "on") == 0 || strcmp(value, "off") == 0 ? 0 : -2;
}

int orender_drain(OrenderRenderer* renderer,
                  float* out,
                  uintptr_t out_cap_samples,
                  uintptr_t* out_frames,
                  uint32_t* out_channels,
                  int64_t* out_pts_us)
{
  FakeRenderer* r = (FakeRenderer*)renderer;
  const uintptr_t samples = 80000;
  *out_frames = 0;
  *out_channels = 2;
  *out_pts_us = 1000;
  if (!r->tail_pending)
    return 0;
  if (out_cap_samples < samples)
    return 1;
  for (uintptr_t i = 0; i < samples; i++)
    out[i] = (i & 1) ? -0.125f : 0.125f;
  r->tail_pending--;
  r->drained = 1;
  *out_frames = samples / 2;
  return 0;
}
