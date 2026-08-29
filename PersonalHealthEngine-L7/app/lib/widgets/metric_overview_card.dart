/// 回看单个健康指标：最新数值、新鲜度、近期趋势图与个人基线参照。
/// 数据来自 /evidence/today 的 all_metrics（与"健康依据"页同源），这里只做回看视角的
/// 紧凑呈现。不展示任何内部术语（置信度、枚举、版本状态）。
library;

import 'package:flutter/material.dart';

import 'trend_chart.dart';

class MetricOverviewCard extends StatelessWidget {
  final Map<String, dynamic> metric;

  const MetricOverviewCard({super.key, required this.metric});

  @override
  Widget build(BuildContext context) {
    final status = '${metric['freshness_status'] ?? 'UNAVAILABLE'}';
    final series = (metric['series'] as List? ?? const [])
        .whereType<Map>()
        .map((item) => item.cast<String, dynamic>())
        .toList();
    final points = <(String, double?)>[
      for (final item in series)
        (
          '${item['local_date'] ?? ''}',
          (item['value_num'] as num?)?.toDouble(),
        ),
    ];

    return Card(
      clipBehavior: Clip.antiAlias,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(
                    color: const Color(0xFFF0E9E0),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Icon(
                    _metricIcon('${metric['key'] ?? ''}'),
                    size: 21,
                    color: const Color(0xFF8A6A4F),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '${metric['label'] ?? '健康指标'}',
                        style: const TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const SizedBox(height: 3),
                      Text(
                        '${metric['value_display'] ?? '暂无数据'}',
                        style: const TextStyle(
                          fontSize: 20,
                          fontWeight: FontWeight.w700,
                          height: 1.2,
                        ),
                      ),
                    ],
                  ),
                ),
                _freshnessChip(status, metric),
              ],
            ),
            const SizedBox(height: 14),
            if (points.isEmpty)
              Text(
                '${metric['availability_note'] ?? '暂无足够的连续数据绘制趋势。'}',
                style: const TextStyle(fontSize: 12.5, color: Colors.black54),
              )
            else ...[
              SizedBox(
                height: 128,
                child: TrendChart(
                  points: points,
                  baselineMedian:
                      (metric['baseline_median'] as num?)?.toDouble(),
                  referenceLow:
                      (metric['reference']?['low'] as num?)?.toDouble(),
                  referenceHigh:
                      (metric['reference']?['high'] as num?)?.toDouble(),
                ),
              ),
              const SizedBox(height: 8),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    '${_md(points.first.$1)} — ${_md(points.last.$1)}',
                    style: const TextStyle(fontSize: 11.5, color: Colors.black54),
                  ),
                  if (metric['baseline_value_display'] != null)
                    Text(
                      '你的通常水平 ${metric['baseline_value_display']}',
                      style: const TextStyle(
                        fontSize: 11.5,
                        color: Colors.black54,
                      ),
                    ),
                ],
              ),
            ],
            if (points.isNotEmpty && false) ...[
              const SizedBox(height: 14),
              const Text(
                '每日数值',
                style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 6),
              Container(
                decoration: BoxDecoration(
                  border: Border.all(color: const Color(0xFFE5E1D8)),
                  borderRadius: BorderRadius.circular(12),
                ),
                padding: const EdgeInsets.symmetric(horizontal: 14),
                child: Column(
                  children: [
                    for (final entry in _recentDays(points))
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Text(
                              _md(entry.$1),
                              style: const TextStyle(
                                  fontSize: 12.5, color: Colors.black87),
                            ),
                            Text(
                              _fmtValue(entry.$2, '${metric['unit'] ?? ''}'),
                              style: const TextStyle(
                                fontSize: 12.5,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
            ],
            if (status == 'STALE') ...[
              const SizedBox(height: 8),
              Text(
                '最新记录是 ${_md('${metric['data_date'] ?? ''}')} 的，还不是今天的数据。',
                style: const TextStyle(
                  fontSize: 11.5,
                  color: Color(0xFFB66A00),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _freshnessChip(String status, Map<String, dynamic> metric) {
    return switch (status) {
      'TODAY' => _chip('今日数据', const Color(0xFFEFECE4), const Color(0xFF191C20)),
      'RECENT' => _chip(
        '数据更新于 ${_md('${metric['data_date'] ?? ''}')}',
        const Color(0xFFEFECE4),
        const Color(0xFF8A8E96),
      ),
      'STALE' => _chip(
        '数据停在 ${_md('${metric['data_date'] ?? ''}')}',
        const Color(0xFFF4E3E0),
        const Color(0xFFC0492E),
      ),
      _ => _chip('暂无数据', const Color(0xFFEFECE4), const Color(0xFF8A8E96)),
    };
  }

  Widget _chip(String label, Color background, Color foreground) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 11,
          color: foreground,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }

  /// 最近 10 天（新→旧）：日期 + 数值。
  List<(String, double)> _recentDays(List<(String, double?)> points) {
    final rows = <(String, double)>[];
    for (final p in points.reversed) {
      final v = p.$2;
      if (v == null) continue;
      rows.add((p.$1, v));
      if (rows.length >= 10) break;
    }
    return rows.reversed.toList();
  }

  String _fmtValue(double? v, String unit) {
    if (v == null) return '暂无';
    switch (unit) {
      case 'seconds':
        final m = (v / 60).round();
        final h = m ~/ 60;
        return h > 0 ? '$h 小时 ${m % 60} 分钟' : '$m 分钟';
      case 'steps':
        return '${v.round()} 步';
      case 'bpm':
        return '${v.toStringAsFixed(1)} 次/分';
      case 'percent':
        return '${v.toStringAsFixed(1)}%';
      case 'ratio':
        return '${(v * 100).toStringAsFixed(1)}%';
      case 'vendor_calories':
        return '${v.round()}（设备记录）';
      case 'vendor_score':
        return '${v.toStringAsFixed(1)}';
      case 'count':
        return '${v.round()} 个';
      default:
        return v.toStringAsFixed(2);
    }
  }

  String _md(String isoDate) {
    final parts = isoDate.split('-');
    if (parts.length != 3) return isoDate;
    final month = int.tryParse(parts[1]);
    final day = int.tryParse(parts[2]);
    if (month == null || day == null) return isoDate;
    return '$month月$day日';
  }

  IconData _metricIcon(String key) {
    return switch (key) {
      'steps' => Icons.directions_walk,
      'active_calories' => Icons.local_fire_department_outlined,
      'sleep' => Icons.bedtime_outlined,
      'heart_rate' => Icons.favorite_border,
      'resting_heart_rate' => Icons.monitor_heart_outlined,
      'spo2' => Icons.water_drop_outlined,
      'stress' => Icons.self_improvement,
      'workouts' => Icons.fitness_center,
      _ => Icons.insights,
    };
  }
}
