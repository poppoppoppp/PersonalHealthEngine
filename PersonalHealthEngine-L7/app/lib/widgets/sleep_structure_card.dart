/// 睡眠结构回看：每晚"睡眠 + 清醒"两段结构的堆叠条与清醒占比。
/// 数据来自小米手环（清醒/睡眠两段；暂无深睡、浅睡、REM 细分，见接口 note）。
library;

import 'package:flutter/material.dart';

class SleepStructureCard extends StatelessWidget {
  final List<Map<String, dynamic>> nights;

  const SleepStructureCard({super.key, required this.nights});

  @override
  Widget build(BuildContext context) {
    if (nights.isEmpty) {
      return const SizedBox.shrink();
    }
    return Card(
      clipBehavior: Clip.antiAlias,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              '睡眠结构（最近每晚）',
              style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 12),
            _NightRow(night: nights.first, highlight: true),
            const SizedBox(height: 14),
            for (final night in nights.skip(1).take(6))
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: _NightRow(night: night),
              ),
            const SizedBox(height: 4),
            Text(
              '蓝色为睡眠时长，橙色为夜间清醒时长。数据来自小米手环，'
              '目前提供清醒/睡眠两段结构，暂无深睡、浅睡、REM 细分。',
              style: const TextStyle(fontSize: 11.5, color: Colors.black45),
            ),
          ],
        ),
      ),
    );
  }
}

class _NightRow extends StatelessWidget {
  final Map<String, dynamic> night;
  final bool highlight;

  const _NightRow({required this.night, this.highlight = false});

  @override
  Widget build(BuildContext context) {
    final sleepMin = (night['sleep_minutes'] as num?)?.toInt() ?? 0;
    final awakeMin = (night['awake_minutes'] as num?)?.toInt() ?? 0;
    final total = sleepMin + awakeMin;
    final awakeShare = total > 0 ? awakeMin / total : 0.0;
    final date = '${night['local_date'] ?? ''}';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                '${_md(date)} · 睡眠 ${_hm(sleepMin)}'
                '${awakeMin > 0 ? ' · 清醒 $awakeMin 分钟' : ''}',
                style: TextStyle(
                  fontSize: highlight ? 13.5 : 12.5,
                  fontWeight: highlight ? FontWeight.w700 : FontWeight.w500,
                  color: Colors.black87,
                ),
              ),
            ),
            if (highlight)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: const Color(0xFFEEF3F8),
                  borderRadius: BorderRadius.circular(999),
                ),
                child: const Text(
                  '昨晚',
                  style: TextStyle(fontSize: 11, color: Color(0xFF33557A)),
                ),
              ),
          ],
        ),
        const SizedBox(height: 5),
        ClipRRect(
          borderRadius: BorderRadius.circular(4),
          child: SizedBox(
            height: highlight ? 12 : 8,
            child: Row(
              children: [
                Expanded(
                  flex: total - awakeMin > 0 ? total - awakeMin : 1,
                  child: Container(color: const Color(0xFF33557A)),
                ),
                if (awakeMin > 0)
                  Expanded(
                    flex: awakeMin,
                    child: Container(color: const Color(0xFFE8A13C)),
                  ),
              ],
            ),
          ),
        ),
        if (highlight && awakeShare > 0)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
              '清醒占比 ${(awakeShare * 100).toStringAsFixed(1)}%'
              '${night['segment_count'] != null ? ' · 记录 ${night['segment_count']} 段' : ''}',
              style: const TextStyle(fontSize: 11.5, color: Colors.black54),
            ),
          ),
      ],
    );
  }

  String _hm(int minutes) {
    final hours = minutes ~/ 60;
    final rest = minutes % 60;
    if (hours <= 0) return '$rest 分钟';
    return '$hours 小时 ${rest.toString().padLeft(2, '0')} 分';
  }

  String _md(String isoDate) {
    final parts = isoDate.split('-');
    if (parts.length != 3) return isoDate;
    final month = int.tryParse(parts[1]);
    final day = int.tryParse(parts[2]);
    if (month == null || day == null) return isoDate;
    return '$month月$day日';
  }
}
