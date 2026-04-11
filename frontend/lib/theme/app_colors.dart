import 'package:flutter/material.dart';

/// Warm child-friendly palette for the ADHD screening app.
///
/// Designed for 6-9 year olds. Parents and clinicians will also see these
/// colors, so they need to feel trustworthy — not cartoonish. The palette
/// is anchored on a soft cream background with a juicy orange primary, a
/// fresh mint secondary, and a warm sunshine yellow accent.
///
/// Reference: 戴尔/有道精品课儿童学习 App 风格
class AppColors {
  AppColors._();

  // ── Surface / background ────────────────────────────────────────────────
  static const Color background = Color(0xFFFFF8F0);      // 暖白
  static const Color surface = Color(0xFFFFFFFF);          // 纯白卡片
  static const Color surfaceContainer = Color(0xFFFFF3E4); // 浅奶油
  static const Color surfaceContainerHigh = Color(0xFFFFE8D1); // 奶油色

  // ── Brand ───────────────────────────────────────────────────────────────
  static const Color primary = Color(0xFFFF8C42);          // 柚子橘
  static const Color primaryContainer = Color(0xFFFFE0CC); // 淡橘
  static const Color onPrimary = Colors.white;
  static const Color onPrimaryContainer = Color(0xFF7A3B15);

  static const Color secondary = Color(0xFF4ECDC4);        // 清新蓝绿
  static const Color secondaryContainer = Color(0xFFCCF3F0);
  static const Color onSecondary = Colors.white;
  static const Color onSecondaryContainer = Color(0xFF1C4541);

  static const Color tertiary = Color(0xFFFFD166);         // 乳黄
  static const Color tertiaryContainer = Color(0xFFFFF0C2);
  static const Color onTertiary = Color(0xFF4A3810);
  static const Color onTertiaryContainer = Color(0xFF4A3810);

  // ── Text ─────────────────────────────────────────────────────────────────
  static const Color onSurface = Color(0xFF2B1810);        // 暖深棕
  static const Color onSurfaceMuted = Color(0xFF8B6F5C);   // 柔和中棕
  static const Color onSurfaceFaint = Color(0xFFC9B29B);   // 极浅

  // ── Status ───────────────────────────────────────────────────────────────
  static const Color error = Color(0xFFE63946);            // 警示红
  static const Color errorContainer = Color(0xFFFFDAD6);
  static const Color onError = Colors.white;
  static const Color onErrorContainer = Color(0xFF5C1215);

  static const Color success = Color(0xFF4ECDC4);          // 用辅色当成功
  static const Color warning = Color(0xFFFF8C42);          // 用主色当注意

  // ── Risk levels (for the ADHD report) ───────────────────────────────────
  /// HIGH  — warm red (柿子红)
  static const Color riskHigh = Color(0xFFE74C3C);
  /// MODERATE — 柚子橘 (same as primary for visual coherence)
  static const Color riskModerate = Color(0xFFFF8C42);
  /// LOW — 乳黄
  static const Color riskLow = Color(0xFFFFD166);
  /// MINIMAL — 清新蓝绿 (same as secondary)
  static const Color riskMinimal = Color(0xFF4ECDC4);

  // ── Borders / dividers ──────────────────────────────────────────────────
  static const Color divider = Color(0xFFF0E0CC);
  static const Color outline = Color(0xFFE0C8A8);
}
