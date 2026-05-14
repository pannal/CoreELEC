/*
 * Additional unusual_uas.h entries backported from mainline Linux v6.6.
 * Appended to the kernel-tree copy by the package's unpack() step, so
 * the customisation lives alongside the out-of-tree uas.ko build that
 * consumes them and never touches the kernel source tree.
 *
 * NOTE: US_FL_NO_SAME is not defined in our 4.9 kernel. The LaCie
 * Rugged USB3-FW entry below carries only US_FL_NO_REPORT_OPCODES
 * (the more impactful of the two flags upstream uses for it).
 */

UNUSUAL_DEV(0x059f, 0x1061, 0x0000, 0x9999,
		"LaCie",
		"Rugged USB3-FW",
		USB_SC_DEVICE, USB_PR_DEVICE, NULL,
		US_FL_NO_REPORT_OPCODES),

UNUSUAL_DEV(0x090c, 0x2000, 0x0000, 0x9999,
		"Hiksemi",
		"External HDD",
		USB_SC_DEVICE, USB_PR_DEVICE, NULL,
		US_FL_IGNORE_UAS),

UNUSUAL_DEV(0x0b05, 0x1932, 0x0000, 0x9999,
		"ASUS",
		"External HDD",
		USB_SC_DEVICE, USB_PR_DEVICE, NULL,
		US_FL_IGNORE_UAS),

UNUSUAL_DEV(0x152d, 0x0583, 0x0000, 0x9999,
		"JMicron",
		"JMS583Gen 2",
		USB_SC_DEVICE, USB_PR_DEVICE, NULL,
		US_FL_NO_REPORT_OPCODES),

UNUSUAL_DEV(0x17ef, 0x3899, 0x0000, 0x9999,
		"Thinkplus",
		"External HDD",
		USB_SC_DEVICE, USB_PR_DEVICE, NULL,
		US_FL_IGNORE_UAS),
