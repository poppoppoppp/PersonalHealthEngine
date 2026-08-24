/// Evidence Level 3: raw values, trends, baselines, quality — only for the metrics that
/// actually deviate in the current judgment (§47 dashboard boundary).
library;

import 'package:flutter/material.dart';

import '../main.dart';
import '../widgets/api_error_view.dart';
import '../widgets/trend_chart.dart';

class EvidenceScreen extends StatefulWidget {
  final AppEnv env;
  const EvidenceScreen({super.key, required this.env});
  @override
  State<EvidenceScreen> createState() => _EvidenceScreenState();
}

class _EvidenceScreenState extends State<EvidenceScreen> {
  Map<String, dynamic>? data;
  Object? error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => error = null);
    try {
      final result = await widget.env.client.getEvidence();
      if (mounted) setState(() => data = result);
    } catch (e) {
      if (mounted) setState(() => error = e);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('依据详情')),
      body: error != null
          ? Center(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: ApiErrorView(error: error!, onRetry: _load),
              ),
            )
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
                Expanded(
                  child: Text('${m['feature_label'] ?? m['metric_label'] ?? '相关指标'}',
                    style: const TextStyle(
                        fontWeight: FontWeight.w700, fontSize: 15)),
                ),
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
                    '${m['deviation_label'] ?? (above ? '高于个人近期基线' : '低于个人近期基线')}',
                    style: TextStyle(
                        fontSize: 11,
                        color:
                            (above ? Colors.deepOrange : Colors.indigo)[800]),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              '${m['baseline_maturity_label'] ?? '个人基线状态未知'} · '
              '${m['evidence_status_label'] ?? '证据状态未知'}',
              style: const TextStyle(fontSize: 11, color: Colors.black54),
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
            Text(
              '当前 ${m['current_value_display'] ?? '暂无数值'} · '
              '个人近期基线 ${m['baseline_value_display'] ?? '暂无数值'}',
              style: const TextStyle(fontSize: 12.5, color: Colors.black87),
            ),
            const SizedBox(height: 4),
            Text(
              '${m['feature_date'] ?? ''} · ${m['freshness_label'] ?? '数据日期未知'}',
              style: const TextStyle(fontSize: 11.5, color: Colors.black54),
            ),
          ],
        ),
      ),
    );
  }

}
