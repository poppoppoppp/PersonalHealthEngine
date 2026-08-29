/// Minimal dependency-free line chart (dates on x, value on y) with an optional
/// personal-baseline median reference line. No chart library needed for MVP.
library;

import 'package:flutter/material.dart';

class TrendChart extends StatelessWidget {
  final List<(String, double?)> points;
  final double? baselineMedian;

  /// 科学参考带（一般成人常见范围）：low/high 为指标自然单位，仅作展示坐标。
  final double? referenceLow;
  final double? referenceHigh;

  const TrendChart({
    super.key,
    required this.points,
    this.baselineMedian,
    this.referenceLow,
    this.referenceHigh,
  });

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      size: Size.infinite,
      painter: _TrendPainter(points: points, baselineMedian: baselineMedian),
    );
  }
}

class _TrendPainter extends CustomPainter {
  final List<(String, double?)> points;
  final double? baselineMedian;
  final double? referenceLow;
  final double? referenceHigh;

  _TrendPainter({
    required this.points,
    this.baselineMedian,
    this.referenceLow,
    this.referenceHigh,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final axis = Paint()..color = Colors.black12;
    canvas.drawLine(
        Offset(0, size.height - 14), Offset(size.width, size.height - 14), axis);

    final values = points
        .map((p) => p.$2)
        .whereType<double>()
        .toList();
    if (values.isEmpty) {
      final tp = TextPainter(
        text: const TextSpan(text: '暂无数据', style: TextStyle(color: Colors.black38)),
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(canvas, Offset(size.width / 2 - tp.width / 2, size.height / 2));
      return;
    }

    var lo = values.reduce((a, b) => a < b ? a : b);
    var hi = values.reduce((a, b) => a > b ? a : b);
    if (baselineMedian != null) {
      lo = lo < baselineMedian! ? lo : baselineMedian!;
      hi = hi > baselineMedian! ? hi : baselineMedian!;
    }
    if (referenceLow != null) {
      lo = lo < referenceLow! ? lo : referenceLow!;
    }
    if (referenceHigh != null) {
      hi = hi > referenceHigh! ? hi : referenceHigh!;
    }
    if (hi - lo < 1e-9) {
      hi = lo + 1;
    }
    final pad = (hi - lo) * 0.15;
    lo -= pad;
    hi += pad;

    double yOf(double v) =>
        size.height - 14 - (v - lo) / (hi - lo) * (size.height - 24);

    if (referenceLow != null && referenceHigh != null) {
      final bandTop = yOf(referenceHigh!);
      final bandBottom = yOf(referenceLow!);
      final band = Paint()..color = const Color(0x248A6A4F);
      canvas.drawRect(
        Rect.fromLTRB(0, bandTop, size.width, bandBottom),
        band,
      );
    }

    if (baselineMedian != null) {
      final ref = Paint()
        ..color = const Color(0xFF8A6A4F).withValues(alpha: 0.6)
        ..strokeWidth = 1
        ..style = PaintingStyle.stroke;
      final y = yOf(baselineMedian!);
      var x = 0.0;
      while (x < size.width) {
        canvas.drawLine(Offset(x, y), Offset(x + 6, y), ref);
        x += 10;
      }
    }

    final line = Paint()
      ..color = const Color(0xFF8A6A4F)
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke;
    final path = Path();
    var pathStarted = false;
    final n = points.length;
    double? firstY;
    for (var i = 0; i < n; i++) {
      final v = points[i].$2;
      final x = n == 1 ? size.width / 2 : i / (n - 1) * (size.width - 8) + 4;
      if (v == null) continue;
      final y = yOf(v);
      firstY ??= y;
      if (!pathStarted) {
        path.moveTo(x, y);
        pathStarted = true;
      } else {
        path.lineTo(x, y);
      }
    }
    canvas.drawPath(path, line);

    final dot = Paint()..color = const Color(0xFF8A6A4F);
    for (var i = 0; i < n; i++) {
      final v = points[i].$2;
      if (v == null) continue;
      final x = n == 1 ? size.width / 2 : i / (n - 1) * (size.width - 8) + 4;
      canvas.drawCircle(Offset(x, yOf(v)), 2.5, dot);
    }

    // First/last date labels.
    TextStyle s = const TextStyle(fontSize: 9, color: Colors.black38);
    if (points.isNotEmpty) {
      final first = TextPainter(
          text: TextSpan(text: points.first.$1, style: s),
          textDirection: TextDirection.ltr)
        ..layout();
      first.paint(canvas, Offset(0, size.height - 12));
      final last = TextPainter(
          text: TextSpan(text: points.last.$1, style: s),
          textDirection: TextDirection.ltr)
        ..layout();
      last.paint(canvas, Offset(size.width - last.width, size.height - 12));
    }
  }

  @override
  bool shouldRepaint(covariant _TrendPainter oldDelegate) =>
      oldDelegate.points != points ||
      oldDelegate.baselineMedian != baselineMedian ||
      oldDelegate.referenceLow != referenceLow ||
      oldDelegate.referenceHigh != referenceHigh;
}
