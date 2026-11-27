// SPDX-License-Identifier: GPL-2.0-or-later
// Optional graphics-forward policy for the exact supported vendor library.
// Version2 transport at CP+19800 (reserved[1]): word[0]=PGX2, [1]=enable,
// [4]=retry. Successful CP writes word[2]=AGX2, [3]=mode|fresh<<1|graphics<<2.
.text
.p2align 4
.global p3i_entry
.type p3i_entry,%function
p3i_entry:
    adrp x16, p3i_state
    add x16, x16, :lo12:p3i_state
    stp wzr, wzr, [x16]
    str wzr, [x16, #12]
    str wzr, [x16, #20]
    str x0, [x16, #24]
    cbz x0, 9f
    ldr w16, [x0]
    cmp w16, #3
    b.ne 9f
    ldr w16, [x0, #4]
    cmp w16, #2
    b.ne 9f
    // A public 3-input/2-video call has one graphics lane, packed last.
    // Recovery remains possible when the stricter opt-in route becomes invalid.
    ldr w16, [x0, #304]
    cmp w16, #1
    b.ne 9f
    adrp x16, p3i_state
    add x16, x16, :lo12:p3i_state
    mov w17, #1
    str w17, [x16]
    // Validate the private request before the stricter opt-in route. A route
    // exit can still acknowledge regeneration of the native graphics curve.
    add x16, x0, #4, lsl #12
    ldr w16, [x16, #3416]
    mov w17, #0x4750
    movk w17, #0x3258, lsl #16
    cmp w16, w17
    b.ne 9f
    add x16, x0, #4, lsl #12
    ldr w16, [x16, #3420]
    cmp w16, #1
    b.hi 9f
    add x16, x0, #4, lsl #12
    ldr w17, [x16, #3432]
    cmp w17, #1
    b.hi 9f
    lsl w17, w17, #1
    orr w17, w17, #1
    adrp x16, p3i_state
    add x16, x16, :lo12:p3i_state
    str w17, [x16, #20]
    add x16, x0, #4, lsl #12
    ldr w16, [x16, #3420]
    cbz w16, 9f
    ldr w16, [x0, #8]
    cbnz w16, 9f
    ldr w16, [x0, #12]
    cbnz w16, 9f
    ldr w16, [x0, #20]
    cbnz w16, 9f
    ldr w16, [x0, #28]
    cbnz w16, 9f
    ldr w16, [x0, #24]
    cbnz w16, 9f
    ldr w16, [x0, #32]
    cbnz w16, 9f
    ldr w16, [x0, #88]
    cbnz w16, 9f
    ldr w16, [x0, #96]
    cmp w16, #1
    b.ne 9f
    ldr w16, [x0, #100]
    cbnz w16, 9f
    ldr w16, [x0, #200]
    cbnz w16, 9f
    ldr w16, [x0, #304]
    cmp w16, #1
    b.ne 9f
    ldr w16, [x0, #308]
    cmp w16, #2
    b.ne 9f
    ldr w16, [x0, #324]
    cmp w16, #8
    b.ne 9f
    ldr w16, [x0, #328]
    cmp w16, #3
    b.ne 9f
    ldr w16, [x0, #332]
    cmp w16, #1
    b.ne 9f
    ldr w16, [x0, #336]
    cmp w16, #1
    b.ne 9f
    ldr w16, [x0, #388]
    cmp w16, #2
    b.ne 9f
    adrp x16, p3i_state
    add x16, x16, :lo12:p3i_state
    mov w17, #1
    str w17, [x16, #4]
9:
    hint #25
    b p3i_resume_cp
.size p3i_entry, .-p3i_entry

.macro dirty_graphics
    stp x16, x17, [sp, #-32]!
    mrs x16, nzcv
    str x16, [sp, #16]
    adrp x16, p3i_state
    add x16, x16, :lo12:p3i_state
    ldr w17, [x16]
    cbz w17, .Lclean\@
    ldr w17, [x21]
    cmp w22, w17
    b.ne .Lclean\@
    ldr w17, [x16, #20]
    tbnz w17, #1, .Ldirty\@
    ldr w17, [x16, #16]
    cbnz w17, .Ldirty\@
    ldr w17, [x16, #4]
    ldr w16, [x16, #8]
    cmp w16, w17
    b.eq .Lclean\@
.Ldirty\@:
    orr w8, w8, #1
.Lclean\@:
    ldr x16, [sp, #16]
    msr nzcv, x16
    ldp x16, x17, [sp], #32
.endm
.p2align 4
.global p3i_ba30
.type p3i_ba30,%function
p3i_ba30:
    dirty_graphics
    cbnz w8, 1f
    b p3i_resume_ba30
1:  b p3i_taken_ba30
.size p3i_ba30, .-p3i_ba30
.p2align 4
.global p3i_bdd8
.type p3i_bdd8,%function
p3i_bdd8:
    dirty_graphics
    cbz w8, 1f
    b p3i_resume_bdd8
1:  b p3i_taken_bdd8
.size p3i_bdd8, .-p3i_bdd8
.p2align 4
.global p3i_curve
.type p3i_curve,%function
p3i_curve:
    adrp x16, p3i_state
    add x16, x16, :lo12:p3i_state
    ldr w17, [x16]
    cbz w17, 9f
    ldr w17, [x21]
    cmp w22, w17
    b.ne 9f
    // Native-only history must retain even its original failure/retry behavior.
    ldr w17, [x16, #4]
    cbnz w17, 8f
    ldr w17, [x16, #8]
    cbnz w17, 8f
    ldr w17, [x16, #16]
    cbz w17, 9f
8:
    // Track uncertainty only while generating or recovering this option.
    mov w17, #1
    str w17, [x16, #16]
    ldr w17, [x16, #4]
    cbz w17, 9f
    mov w16, #2
    str w16, [x0, #228]
9:  b CreateToneCurve
.size p3i_curve, .-p3i_curve

.p2align 4
.global p3i_concat_done
.type p3i_concat_done,%function
p3i_concat_done:
    stp x16, x17, [sp, #-32]!
    mrs x16, nzcv
    str x16, [sp, #16]
    adrp x16, p3i_state
    add x16, x16, :lo12:p3i_state
    ldr w17, [x16]
    cbz w17, 9f
    ldr w17, [x21]
    cmp w22, w17
    b.ne 9f
    ldr w17, [x16, #4]
    add w17, w17, #1
    str w17, [x16, #12]
9:
    ldr x16, [sp, #16]
    msr nzcv, x16
    ldp x16, x17, [sp], #32
    mov w26, wzr
    b p3i_resume_concat
.size p3i_concat_done, .-p3i_concat_done

.p2align 4
.global p3i_cp_done
.type p3i_cp_done,%function
p3i_cp_done:
    stp x16, x17, [sp, #-32]!
    mrs x16, nzcv
    str x16, [sp, #16]
    tbnz w21, #31, 9f
    adrp x16, p3i_state
    add x16, x16, :lo12:p3i_state
    ldr w17, [x16, #12]
    cbz w17, 8f
    sub w17, w17, #1
    str w17, [x16, #8]
    str wzr, [x16, #16]
8:
    ldr w17, [x16, #20]
    cbz w17, 9f
    ldr x0, [x16, #24]
    add x0, x0, #4, lsl #12
    mov w17, #0x4741
    movk w17, #0x3258, lsl #16
    str w17, [x0, #3424]
    ldr w17, [x16, #8]
    orr w17, w17, #4
    ldr w16, [x16, #12]
    cbz w16, 7f
    orr w17, w17, #2
7:
    str w17, [x0, #3428]
9:
    ldr x16, [sp, #16]
    msr nzcv, x16
    ldp x16, x17, [sp], #32
    mov w0, w21
    b p3i_resume_done
.size p3i_cp_done, .-p3i_cp_done
