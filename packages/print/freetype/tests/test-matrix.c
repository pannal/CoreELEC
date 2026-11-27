/* Bounded valid-matrix and optional attachment-glyph checks for patched FreeType.
 * Link with the corrected ftbase.o and remaining package module objects.
 * No faulty original, ASS allocation replay or device rendering is exercised.
 */
#include <stdio.h>
#include <ft2build.h>
#include FT_FREETYPE_H

extern FT_Bool FT_Matrix_Check(const FT_Matrix* matrix);

static int check(int condition, const char* label)
{
  if (condition)
    return 0;
  fprintf(stderr, "FAIL: %s\n", label);
  return 1;
}

int main(int argc, char** argv)
{
  const FT_Matrix matrices[] = {
      {0, 0, 0, 0}, {65536, 65536, 65536, 65536},
      {65536, 0, 0, 65536}, {32768, 0, 0, 32768},
      {1000, 0, 0, 1000}, {4096, 0, 0, 4096},
      {0, -65536, 65536, 0}, {65536, 16384, 0, 65536}};
  const int expected[] = {0, 0, 1, 1, 1, 1, 1, 1};
  unsigned int i;
  int failed = check(!FT_Matrix_Check(NULL), "null matrix");
  for (i = 0; i < sizeof(matrices) / sizeof(matrices[0]); ++i)
    failed += check(!!FT_Matrix_Check(&matrices[i]) == expected[i], "matrix contract");
  if (failed)
    return 1;
  puts("PASS: 9 corrected matrix contracts");
  if (argc == 2)
  {
    FT_Library library;
    FT_Face face;
    const char* text = "THE";
    if (FT_Init_FreeType(&library))
      return 2;
    if (FT_New_Face(library, argv[1], 0, &face))
    {
      FT_Done_FreeType(library);
      return 2;
    }
    failed += check(face->units_per_EM == 1000, "attachment UPEM");
    failed += check(FT_Set_Pixel_Sizes(face, 0, 100) == 0, "100px glyph size");
    /* Verify normal scaling before any bitmap allocation. */
    if (failed)
      goto done;
    for (i = 0; text[i]; ++i)
    {
      FT_UInt glyph = FT_Get_Char_Index(face, (unsigned char)text[i]);
      failed += check(glyph != 0, "attachment glyph exists");
      failed += check(FT_Load_Glyph(face, glyph, FT_LOAD_NO_HINTING) == 0, "glyph load");
      if (failed)
        break;
      failed += check(face->glyph->metrics.width > 0 && face->glyph->metrics.width < 512 * 64 &&
                      face->glyph->metrics.height > 0 && face->glyph->metrics.height < 512 * 64,
                      "bounded glyph metrics");
      if (failed)
        break;
      failed += check(FT_Render_Glyph(face->glyph, FT_RENDER_MODE_NORMAL) == 0, "glyph render");
      failed += check(face->glyph->bitmap.width > 0 && face->glyph->bitmap.width < 512 &&
                      face->glyph->bitmap.rows > 0 && face->glyph->bitmap.rows < 512,
                      "bounded glyph bitmap");
      if (failed)
        break;
      printf("PASS: attachment glyph %c %u x %u\n", text[i],
             face->glyph->bitmap.width, face->glyph->bitmap.rows);
    }
  done:
    FT_Done_Face(face);
    FT_Done_FreeType(library);
  }
  else if (argc != 1)
    return 2;
  return failed ? 1 : 0;
}
