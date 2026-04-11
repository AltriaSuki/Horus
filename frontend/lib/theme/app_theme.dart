import 'package:flutter/material.dart';

import 'app_colors.dart';

/// Material 3 theme for the ADHD screening app.
///
/// Design decisions baked in here (don't override ad-hoc in widgets):
///
/// * **Large rounded corners** — cards 20px, buttons 28px (pill).
/// * **Pill-shaped primary action buttons** — easy for kids to hit.
/// * **Generous padding** — 20-28px default, never squish content.
/// * **Warm typography** — PingFang SC / Microsoft YaHei fallback chain.
/// * **Body 16px, headlines 28-40px** — large enough for 6-9 year olds to read.
class AppTheme {
  AppTheme._();

  // Bundled Chinese font (see pubspec.yaml). Flutter Web / CanvasKit
  // cannot access system fonts so we ship our own — otherwise Windows
  // browsers render all Chinese text as tofu □□□.
  //
  // NotoSansSC is a variable font with weights 100-900. We register it
  // for the weights we actually use (400/500/600/700/800) so Flutter
  // doesn't synthesise fake bold.
  //
  // The OS-native rounded options (PingFang SC / Microsoft YaHei) are
  // kept in ``fontFamilyFallback`` as a safety net for native builds.
  static const String _fontFamily = 'NotoSansSC';
  static const List<String> _fallbackFonts = [
    'PingFang SC',
    'Microsoft YaHei',
    'Heiti SC',
    'Noto Sans CJK SC',
  ];

  static ThemeData build() {
    const scheme = ColorScheme(
      brightness: Brightness.light,
      primary: AppColors.primary,
      onPrimary: AppColors.onPrimary,
      primaryContainer: AppColors.primaryContainer,
      onPrimaryContainer: AppColors.onPrimaryContainer,
      secondary: AppColors.secondary,
      onSecondary: AppColors.onSecondary,
      secondaryContainer: AppColors.secondaryContainer,
      onSecondaryContainer: AppColors.onSecondaryContainer,
      tertiary: AppColors.tertiary,
      onTertiary: AppColors.onTertiary,
      tertiaryContainer: AppColors.tertiaryContainer,
      onTertiaryContainer: AppColors.onTertiaryContainer,
      error: AppColors.error,
      onError: AppColors.onError,
      errorContainer: AppColors.errorContainer,
      onErrorContainer: AppColors.onErrorContainer,
      surface: AppColors.background,
      onSurface: AppColors.onSurface,
      surfaceContainerHighest: AppColors.surfaceContainerHigh,
      surfaceContainerHigh: AppColors.surfaceContainer,
      surfaceContainer: AppColors.surfaceContainer,
      surfaceContainerLow: AppColors.surface,
      surfaceContainerLowest: AppColors.surface,
      onSurfaceVariant: AppColors.onSurfaceMuted,
      outline: AppColors.outline,
      outlineVariant: AppColors.divider,
      shadow: Color(0x1A2B1810),
      scrim: Color(0x662B1810),
      inverseSurface: AppColors.onSurface,
      onInverseSurface: AppColors.background,
      inversePrimary: Color(0xFFFFBE96),
    );

    const displayTextTheme = TextTheme(
      displayLarge: TextStyle(fontSize: 44, fontWeight: FontWeight.w700,
          color: AppColors.onSurface, letterSpacing: -0.5),
      displayMedium: TextStyle(fontSize: 36, fontWeight: FontWeight.w700,
          color: AppColors.onSurface, letterSpacing: -0.3),
      displaySmall: TextStyle(fontSize: 28, fontWeight: FontWeight.w700,
          color: AppColors.onSurface),
      headlineLarge: TextStyle(fontSize: 28, fontWeight: FontWeight.w700,
          color: AppColors.onSurface),
      headlineMedium: TextStyle(fontSize: 24, fontWeight: FontWeight.w700,
          color: AppColors.onSurface),
      headlineSmall: TextStyle(fontSize: 20, fontWeight: FontWeight.w700,
          color: AppColors.onSurface),
      titleLarge: TextStyle(fontSize: 20, fontWeight: FontWeight.w600,
          color: AppColors.onSurface),
      titleMedium: TextStyle(fontSize: 17, fontWeight: FontWeight.w600,
          color: AppColors.onSurface),
      titleSmall: TextStyle(fontSize: 15, fontWeight: FontWeight.w600,
          color: AppColors.onSurfaceMuted),
      bodyLarge: TextStyle(fontSize: 16, fontWeight: FontWeight.w400,
          color: AppColors.onSurface, height: 1.6),
      bodyMedium: TextStyle(fontSize: 15, fontWeight: FontWeight.w400,
          color: AppColors.onSurface, height: 1.5),
      bodySmall: TextStyle(fontSize: 13, fontWeight: FontWeight.w400,
          color: AppColors.onSurfaceMuted, height: 1.4),
      labelLarge: TextStyle(fontSize: 15, fontWeight: FontWeight.w600,
          color: AppColors.onSurface),
      labelMedium: TextStyle(fontSize: 13, fontWeight: FontWeight.w600,
          color: AppColors.onSurfaceMuted),
      labelSmall: TextStyle(fontSize: 11, fontWeight: FontWeight.w600,
          color: AppColors.onSurfaceMuted, letterSpacing: 0.5),
    );

    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: AppColors.background,
      fontFamily: _fontFamily,
      fontFamilyFallback: _fallbackFonts,
      textTheme: displayTextTheme,
      primaryTextTheme: displayTextTheme,

      // App bar — flat, warm, no shadow
      appBarTheme: const AppBarTheme(
        backgroundColor: AppColors.background,
        foregroundColor: AppColors.onSurface,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: TextStyle(
          fontSize: 22,
          fontWeight: FontWeight.w700,
          color: AppColors.onSurface,
          fontFamily: _fontFamily,
          fontFamilyFallback: _fallbackFonts,
        ),
      ),

      // Cards — big rounded, soft shadow, cream surface
      cardTheme: CardThemeData(
        color: AppColors.surface,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: const BorderSide(color: AppColors.divider, width: 1),
        ),
      ),

      // Primary action buttons — big and pill-shaped
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: AppColors.primary,
          foregroundColor: AppColors.onPrimary,
          minimumSize: const Size(0, 56),
          padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 16),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(28),
          ),
          textStyle: const TextStyle(
            fontSize: 17,
            fontWeight: FontWeight.w700,
            fontFamily: _fontFamily,
            fontFamilyFallback: _fallbackFonts,
          ),
        ),
      ),

      // Secondary buttons
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.primary,
          side: const BorderSide(color: AppColors.primary, width: 2),
          minimumSize: const Size(0, 52),
          padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 14),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(28),
          ),
          textStyle: const TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            fontFamily: _fontFamily,
            fontFamilyFallback: _fallbackFonts,
          ),
        ),
      ),

      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: AppColors.primary,
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(20),
          ),
          textStyle: const TextStyle(
            fontSize: 15,
            fontWeight: FontWeight.w600,
            fontFamily: _fontFamily,
            fontFamilyFallback: _fallbackFonts,
          ),
        ),
      ),

      // Input fields — pill-ish, warm
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: AppColors.surfaceContainer,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 20, vertical: 18),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(18),
          borderSide: BorderSide.none,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(18),
          borderSide: const BorderSide(color: AppColors.divider, width: 1),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(18),
          borderSide: const BorderSide(color: AppColors.primary, width: 2),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(18),
          borderSide: const BorderSide(color: AppColors.error, width: 2),
        ),
        labelStyle: const TextStyle(
          color: AppColors.onSurfaceMuted,
          fontSize: 15,
          fontWeight: FontWeight.w500,
          fontFamily: _fontFamily,
          fontFamilyFallback: _fallbackFonts,
        ),
        hintStyle: const TextStyle(
          color: AppColors.onSurfaceFaint,
          fontSize: 15,
          fontFamily: _fontFamily,
          fontFamilyFallback: _fallbackFonts,
        ),
      ),

      // Bottom nav bar — warm cream with orange active pill
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: AppColors.surface,
        elevation: 0,
        indicatorColor: AppColors.primaryContainer,
        indicatorShape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(24),
        ),
        height: 76,
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          final isSelected = states.contains(WidgetState.selected);
          return TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.w600,
            fontFamily: _fontFamily,
            fontFamilyFallback: _fallbackFonts,
            color: isSelected
                ? AppColors.primary
                : AppColors.onSurfaceMuted,
          );
        }),
        iconTheme: WidgetStateProperty.resolveWith((states) {
          final isSelected = states.contains(WidgetState.selected);
          return IconThemeData(
            size: 26,
            color: isSelected
                ? AppColors.primary
                : AppColors.onSurfaceMuted,
          );
        }),
      ),

      // Dividers
      dividerTheme: const DividerThemeData(
        color: AppColors.divider,
        thickness: 1,
        space: 1,
      ),

      // Dialog
      dialogTheme: DialogThemeData(
        backgroundColor: AppColors.surface,
        elevation: 2,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(24),
        ),
        titleTextStyle: const TextStyle(
          fontSize: 22,
          fontWeight: FontWeight.w700,
          color: AppColors.onSurface,
          fontFamily: _fontFamily,
          fontFamilyFallback: _fallbackFonts,
        ),
        contentTextStyle: const TextStyle(
          fontSize: 15,
          fontWeight: FontWeight.w400,
          color: AppColors.onSurface,
          height: 1.5,
          fontFamily: _fontFamily,
          fontFamilyFallback: _fallbackFonts,
        ),
      ),

      // Chip (used for status tags)
      chipTheme: ChipThemeData(
        backgroundColor: AppColors.surfaceContainer,
        labelStyle: const TextStyle(
          fontSize: 13,
          fontWeight: FontWeight.w600,
          color: AppColors.onSurface,
          fontFamily: _fontFamily,
          fontFamilyFallback: _fallbackFonts,
        ),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
        ),
        side: BorderSide.none,
      ),

      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: AppColors.primary,
        linearTrackColor: AppColors.surfaceContainerHigh,
        circularTrackColor: AppColors.surfaceContainerHigh,
      ),

      // SnackBar
      snackBarTheme: SnackBarThemeData(
        backgroundColor: AppColors.onSurface,
        contentTextStyle: const TextStyle(
          color: AppColors.background,
          fontSize: 15,
          fontFamily: _fontFamily,
          fontFamilyFallback: _fallbackFonts,
        ),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
        ),
        behavior: SnackBarBehavior.floating,
      ),

      // FAB (we use extended floating action buttons on subjects page)
      floatingActionButtonTheme: const FloatingActionButtonThemeData(
        backgroundColor: AppColors.primary,
        foregroundColor: AppColors.onPrimary,
        elevation: 2,
        extendedTextStyle: TextStyle(
          fontSize: 15,
          fontWeight: FontWeight.w700,
          fontFamily: _fontFamily,
          fontFamilyFallback: _fallbackFonts,
        ),
      ),

      splashFactory: InkSparkle.splashFactory,
    );
  }
}
