/// 晨间简报报头：品牌行 + 日期行 + 粗细双线。每个主页面共用，替换 AppBar。
library;

import 'package:flutter/material.dart';

import '../design.dart';

class Masthead extends StatelessWidget {
  final String brand;
  final String title;
  final String? trailing;

  const Masthead({
    super.key,
    required this.brand,
    required this.title,
    this.trailing,
  });

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    const weekdays = ['一', '二', '三', '四', '五', '六', '日'];
    final dateText =
        '${now.year}年${now.month}月${now.day}日 · 星期${weekdays[now.weekday - 1]}';
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            brand,
            style: const TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w700,
              letterSpacing: 3,
              color: Ed.seal,
            ),
          ),
          const SizedBox(height: 3),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Expanded(
                child: Text(
                  title,
                  style: const TextStyle(fontSize: 12, color: Ed.inkSoft),
                ),
              ),
              if (trailing != null)
                Text(
                  trailing!,
                  style: const TextStyle(fontSize: 12, color: Ed.inkSoft),
                ),
            ],
          ),
          Text(dateText, style: const TextStyle(fontSize: 12, color: Ed.inkSoft)),
          const SizedBox(height: 8),
          Container(height: 3, color: Ed.ink),
          const SizedBox(height: 2),
          Container(height: 1, color: Ed.ink),
        ],
      ),
    );
  }
}

/// 版面小节标题：蓝色字距标签 + 延伸细线（如「今日要闻」）。
class SectionLabel extends StatelessWidget {
  final String text;
  const SectionLabel(this.text, {super.key});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 18, bottom: 10),
      child: Row(
        children: [
          Text(
            text,
            style: const TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w800,
              letterSpacing: 2,
              color: Ed.ink,
            ),
          ),
          const SizedBox(width: 8),
          const Expanded(child: DashedLine(color: Ed.hairline)),
        ],
      ),
    );
  }
}

class DashedLine extends StatelessWidget {
  final Color color;
  final double dashWidth;
  final double gap;
  const DashedLine({super.key, this.color = Ed.hairline, this.dashWidth = 4, this.gap = 3});

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final count = (constraints.maxWidth / (dashWidth + gap)).floor();
        return Row(
          children: [
            for (var i = 0; i < count; i++)
              Container(
                width: dashWidth,
                height: 1,
                margin: EdgeInsets.only(right: gap),
                color: color,
              ),
          ],
        );
      },
    );
  }
}
