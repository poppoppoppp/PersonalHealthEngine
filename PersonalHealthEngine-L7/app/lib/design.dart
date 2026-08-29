/// 晨间简报设计系统 —— 全 app 共用的颜色与字形令牌。
/// 纸感底色 + 墨色文字 + 暖棕数据 + 印章红点缀；对应 2026-08-29 定稿效果图。
library;

import 'package:flutter/material.dart';

class Ed {
  Ed._();

  // 底与墨
  static const paper = Color(0xFFFAF8F4); // 纸白底
  static const ink = Color(0xFF191C20); // 墨色主文字
  static const inkSoft = Color(0xFF5C6066); // 次级文字
  static const inkFaint = Color(0xFFA0A4AB); // 页脚小字

  // 卡片与分隔
  static const card = Colors.white;
  static const hairline = Color(0xFFE5E1D8); // 细分隔线与卡片描边

  // 强调
  static const seal = Color(0xFFC0492E); // 印章红：状态标签、关键点缀
  static const sealTint = Color(0xFFF4E3E0); // 印章红的浅底

  // 数据（暖棕油墨）
  static const data = Color(0xFF8A6A4F); // 柱状图、选中标签、趋势线
  static const dataSoft = Color(0xFFCFC3B2); // 弱数据柱
  static const dataTint = Color(0xFFF0E9E0); // 数据色浅底

  // 状态
  static const good = Color(0xFF2F6E5D); // 已完成/正常
  static const warn = Color(0xFFB66A00); // 需注意

  static ThemeData theme() {
    final scheme = ColorScheme.fromSeed(seedColor: data).copyWith(
      primary: ink,
      onPrimary: paper,
      secondary: seal,
      surface: card,
    );
    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: paper,
      cardTheme: CardThemeData(
        color: card,
        elevation: 0,
        shadowColor: Colors.transparent,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: const BorderSide(color: hairline),
        ),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: card,
        indicatorColor: dataTint,
        surfaceTintColor: Colors.transparent,
        labelTextStyle: const WidgetStatePropertyAll(
          TextStyle(fontSize: 10.5, fontWeight: FontWeight.w600),
        ),
        iconTheme: const WidgetStatePropertyAll(IconThemeData(color: inkSoft)),
      ),
    );
  }
}
