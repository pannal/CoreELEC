/* A stand-in engine for test_protocol.py. Built twice:
 *
 *   cc -shared -fPIC -I<orender_ffi/include> -o libfake_orender.so fake_orender.c
 *   cc -shared -fPIC -I<orender_ffi/include> -DFAKE_INCOMPLETE \
 *      -o libfake_orender_incomplete.so fake_orender.c
 *
 * The second is a broken install, an engine missing entry points the helper
 * calls: no orender_brir_prepare, orender_brir_state, orender_check_config,
 * orender_sofa_describe, orender_hrtf_prepare, render path or latency query,
 * which the helper refuses by name. With FAKE_ORENDER_LOG set,
 * orender_create and orender_set_option append what they were handed to that
 * file, one line each. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "orender.h"

typedef struct
{
  int tail_pending;
  int drained;
  int source_present;
} FakeRenderer;

static void fake_log(const char* fmt, const char* a, const char* b)
{
  const char* path = getenv("FAKE_ORENDER_LOG");
  FILE* f = path ? fopen(path, "a") : NULL;
  if (!f)
    return;
  fprintf(f, fmt, a ? a : "(null)", b ? b : "(null)");
  fputc('\n', f);
  fclose(f);
}

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
  fake_log("create config=%s layout=%s", config->config_yaml_path, config->speaker_layout_path);
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
  fake_log("set_option %s=%s", key, value);
  if (strcmp(key, "decode_thread") != 0)
    return -1;
  return strcmp(value, "on") == 0 || strcmp(value, "off") == 0 || strcmp(value, "live") == 0
             ? 0
             : -2;
}

#ifndef FAKE_INCOMPLETE
/* The room loads off the audio thread a moment into the stream: loading
 * while the first stream's frames come out, resident from the next FEED on,
 * and the convolution's latency with it - the change the helper must pass on. */
int orender_brir_state(const OrenderRenderer* renderer)
{
  const FakeRenderer* r = (const FakeRenderer*)renderer;
  return r->source_present ? 1 : 2;
}

uint64_t orender_output_latency_samples(const OrenderRenderer* renderer)
{
  const FakeRenderer* r = (const FakeRenderer*)renderer;
  return r->source_present ? 0 : 127;
}

/* The render follows the room: its loudspeakers cascaded for the stand-in
 * while it loads, then the room itself. */
uint32_t orender_render_path(const OrenderRenderer* renderer, char* out, uint32_t cap)
{
  const FakeRenderer* r = (const FakeRenderer*)renderer;
  const char* path = r->source_present ? "cascade:3" : "room:3";
  const uint32_t n = (uint32_t)strlen(path);
  if (out && cap > n)
    memcpy(out, path, n + 1);
  return n;
}

static int fake_report(char* out, uint32_t cap, const char* text)
{
  if (out && cap)
    snprintf(out, cap, "%s", text);
  return 0;
}

/* A config reading "reject" is refused, one that cannot be read too; any
 * other is accepted. The file is only read. */
int orender_check_config(const char* path, char* report, uint32_t cap)
{
  if (!path)
    return -3;
  FILE* f = fopen(path, "r");
  if (!f)
  {
    fake_report(report, cap, "fake: cannot read it");
    return -1;
  }
  char text[256] = "";
  const size_t n = fread(text, 1, sizeof(text) - 1, f);
  fclose(f);
  text[n] = '\0';
  if (strstr(text, "reject"))
  {
    fake_report(report, cap, "fake: the config says reject");
    return -1;
  }
  fake_report(report, cap, "");
  return 0;
}

/* Bytes starting "BAD" are not a room, "HRTF" an HRTF set; a kept file that
 * holds the same bytes is kept; anything else prepares, the line the
 * description with the room's figures before its reason. */
int orender_brir_prepare(const uint8_t* sofa,
                         uintptr_t len,
                         const char* kept_path,
                         const char* out_path,
                         char* summary,
                         uint32_t cap)
{
  if (!sofa || !out_path)
    return -3;
  if (len >= 3 && memcmp(sofa, "BAD", 3) == 0)
  {
    fake_report(summary, cap, "fake: not a room response");
    return -1;
  }
  if (len >= 4 && memcmp(sofa, "HRTF", 4) == 0)
  {
    fake_report(summary, cap,
                "hrtf=yes room=no prepared=no conventions=SimpleFreeFieldHRIR measurements=2 "
                "receivers=2 emitters=1 samples=256 rate=48000 source=8-fake "
                "reason=fake: one direction each");
    return 2;
  }
  char head[512];
  snprintf(head, sizeof(head),
           "hrtf=no room=yes prepared=no conventions=MultiSpeakerBRIR measurements=1 "
           "receivers=2 emitters=3 samples=12000 rate=48000 source=%lu-fake orientations=1 "
           "speakers=3 names=FL,FR,C",
           (unsigned long)len);
  const char* reason = " reason=fake: 3 loudspeakers in every measurement";
  char line[1024];
  FILE* kept = kept_path ? fopen(kept_path, "rb") : NULL;
  if (kept)
  {
    uint8_t* have = (uint8_t*)malloc(len + 1);
    const size_t got = have ? fread(have, 1, len + 1, kept) : 0;
    fclose(kept);
    const int same = have && got == len && memcmp(have, sofa, len) == 0;
    free(have);
    if (same)
    {
      snprintf(line, sizeof(line), "%s%s", head, reason);
      fake_report(summary, cap, line);
      return 1;
    }
  }
  FILE* out = fopen(out_path, "wb");
  if (!out)
  {
    fake_report(summary, cap, "fake: cannot write");
    return -2;
  }
  fwrite(sofa, 1, len, out);
  fclose(out);
  snprintf(line, sizeof(line), "%s seconds=0.250 bytes=%lu%s", head, (unsigned long)len, reason);
  fake_report(summary, cap, line);
  return 0;
}

/* A set whose file starts "BAD" is refused; otherwise the grid file holds
 * the rate and setting, and one that already does is kept. */
int orender_hrtf_prepare(const char* sofa_path,
                         const char* grid_path,
                         uint32_t sample_rate,
                         int diffuse_field_eq,
                         char* summary,
                         uint32_t cap)
{
  if (!sofa_path || !grid_path)
    return -3;
  char head[4] = "";
  FILE* in = fopen(sofa_path, "rb");
  const size_t got = in ? fread(head, 1, sizeof(head), in) : 0;
  if (in)
    fclose(in);
  if (!in || (got >= 3 && memcmp(head, "BAD", 3) == 0))
  {
    fake_report(summary, cap, "fake: not an HRTF set");
    return -1;
  }
  char want[64];
  snprintf(want, sizeof(want), "grid %u %d", sample_rate, diffuse_field_eq);
  char have[64] = "";
  FILE* grid = fopen(grid_path, "rb");
  if (grid)
  {
    have[fread(have, 1, sizeof(have) - 1, grid)] = '\0';
    fclose(grid);
    if (strcmp(have, want) == 0)
    {
      fake_report(summary, cap, "grid=kept bytes=16");
      return 1;
    }
  }
  grid = fopen(grid_path, "wb");
  if (!grid)
  {
    fake_report(summary, cap, "fake: cannot write");
    return -2;
  }
  fputs(want, grid);
  fclose(grid);
  fake_report(summary, cap, "grid=built seconds=0.100 bytes=16");
  return 0;
}

/* Bytes starting "BAD" are no SOFA file, "HRTF" an HRTF set, anything else
 * a room. */
int orender_sofa_describe(const uint8_t* sofa, uintptr_t len, char* out, uint32_t cap)
{
  if (!sofa || !out)
    return -3;
  if (len >= 3 && memcmp(sofa, "BAD", 3) == 0)
  {
    fake_report(out, cap, "reason=fake: not a SOFA file");
    return -1;
  }
  if (len >= 4 && memcmp(sofa, "HRTF", 4) == 0)
  {
    fake_report(out, cap,
                "hrtf=yes room=no prepared=no conventions=SimpleFreeFieldHRIR measurements=2 "
                "receivers=2 emitters=1 samples=256 rate=48000 source=8-fake "
                "reason=fake: one direction each");
    return 1;
  }
  fake_report(out, cap,
              "hrtf=no room=yes prepared=no conventions=MultiSpeakerBRIR measurements=1 "
              "receivers=2 emitters=3 samples=12000 rate=48000 source=400-fake orientations=1 "
              "speakers=3 names=FL,FR,C reason=fake: 3 loudspeakers in every measurement");
  return 2;
}
#endif

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
