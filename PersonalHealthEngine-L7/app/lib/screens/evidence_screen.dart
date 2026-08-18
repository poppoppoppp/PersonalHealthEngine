/// Evidence Level 3: raw values, trends, baselines, quality — only for the metrics that
/// actually deviate in the current judgment (§47 dashboard boundary).
library;

import 'package:flutter/material.dart';

import '../main.dart';
import '../widgets/trend_chart.dart';

class EvidenceScreen extends StatefulWidget {
  final AppEnv env;
  const EvidenceScreen({super.key, required this.env});
  @override
  State<EvidenceScreen> createState() => _EvidenceScreenState();
}

class _EvidenceScreenState extends State<EvidenceScreen> {
  Map<String, dynamic>? data;
  String? error;

  @override
  void initState() {
    super.initState();
    widget.env.client.getEvidence().then((r) {
      if (mounted) setState(() => data = r);
    }).catchError((e) {
      if (mounted) setState(() => error = '$e');
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('依据详情')),
      body: error != null
          ? Center(child: Text(error!))
          : data == null
              ? const Center(child: CircularProgressIndicator())
              : ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    Text(
                      '分析日期 ${data!['analysis_date']} · 数值只展示与当前判断相关的偏离指标。',
                      style:
                          const TextStyle(fontSize: 12, color: Colors.black54),
                    ),
                    const SizedBox(height: 8),
                    Text('${data!['provenance_note'] ?? ''}',
                        style: const TextStyle(
                            fontSize: 11, color: Colors.black38)),
                    const SizedBox(height: 12),
                    for (final m in (data!['metrics'] as List))
                      _metricCard((m as Map).cast<String, dynamic>()),
                    if ((data!['metrics'] as List).isEmpty)
                      const Card(
                          child: Padding(
                              padding: EdgeInsets.all(16),
                              child: Text('当前没有偏离指标。'))),
                  ],
                ),
    );
  }

  Widget _metricCard(Map<String, dynamic> m) {
    final series = (m['series'] as List)
        .map((e) => (e as Map).cast<String, dynamic>())
        .toList()
        .reversed
        .toList();
    final points = <(String, double?)>[
      for (final s in series)
        (s['local_date'] as String? ?? '', (s['value_num'] as num?)?.toDouble()),
    ];
    final baselines = (m['baselines'] as List)
        .map((e) => (e as Map).cast<String, dynamic>())
        .toList();
    final devs = (m['deviations'] as List)
        .map((e) => (e as Map).cast<String, dynamic>())
        .toList();
    final above = m['deviation_class'] == 'ABOVE_TYPICAL_RANGE';

    return Card(
      margin: const EdgeInsets.only(bottom: 16),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('${m['metric_label']}',
                    style: const TextStyle(
                        fontWeight: FontWeight.w700, fontSize: 15)),
                const SizedBox(width: 8),
                Container(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: (above ? Colors.deepOrange : Colors.indigo)
                        .withOpacity(0.12),
                    borderRadius: BorderRadius.circular(999),
                  ),
                  child: Text(
                    above ? '高于通常水平' : '低于通常水平',
                    style: TextStyle(
                        fontSize: 11,
                        color:
                            (above ? Colors.deepOrange : Colors.indigo)[800]),
                  ),
                ),
                const Spacer(),
                Text('${m['baseline_maturity'] ?? ''}',
                    style:
                        const TextStyle(fontSize: 10, color: Colors.black38)),
              ],
            ),
            const SizedBox(height: 12),
            SizedBox(
              height: 120,
              child: TrendChart(
                points: points,
                baselineMedian: baselines.isNotEmpty
                    ? (baselines.first['median'] as num?)?.toDouble()
                    : null,
              ),
            ),
            const SizedBox(height: 12),
            if (baselines.isNotEmpty)
              Text(
                '你的基线（${baselines.first['window_days']} 天窗口）：'
                '中位数 ${_fmt(baselines.first['median'])}'
                '${baselines.first['mad'] != null ? ' · 波动幅度 ${_fmt(baselines.first['mad'])}' : ''}'
                ' · ${baselines.first['unit'] ?? ''}',
                style: const TextStyle(fontSize: 12, color: Colors.black54),
              ),
            if (devs.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(
                '最新值 ${_fmt(devs.first['current_value'])}（${devs.first['feature_date']}）'
                ' · 数据质量：${devs.first['evidence_status'] ?? '-'}',
                style: const TextStyle(fontSize: 12, color: Colors.black54),
              ),
            ],
            const SizedBox(height: 4),
            const Text(
              '统计方法（MAD / robust z / Theil-Sen）仅在后台使用，不用于打分。',
              style: TextStyle(fontSize: 10, color: Colors.black38),
            ),
          ],
        ),
      ),
    );
  }

  String _fmt(Object? v) {
    if (v == null) return '-';
    final d = (v as num).toDouble();
    return d == d.roundToDouble() ? d.toStringAsFixed(0) : d.toStringAsFixed(1);
  }
}
