/// Readable, metric-first evidence and data freshness view.
library;

import 'package:flutter/material.dart';

import '../design.dart';
import '../main.dart';
import '../widgets/api_error_view.dart';
import '../widgets/trend_chart.dart';

enum _EvidenceFilter { used, all, needsUpdate }

class EvidenceScreen extends StatefulWidget {
  final AppEnv env;
  const EvidenceScreen({super.key, required this.env});

  @override
  State<EvidenceScreen> createState() => _EvidenceScreenState();
}

class _EvidenceScreenState extends State<EvidenceScreen> {
  Map<String, dynamic>? data;
  Object? error;
  _EvidenceFilter filter = _EvidenceFilter.used;
  final Set<String> expanded = {};

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
      appBar: AppBar(title: const Text('健康依据')),
      body: error != null
          ? Center(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: ApiErrorView(error: error!, onRetry: _load),
              ),
            )
          : data == null
          ? const Center(child: CircularProgressIndicator())
          : _body(context),
    );
  }

  Widget _body(BuildContext context) {
    final metrics = (data!['all_metrics'] as List? ?? const [])
        .whereType<Map>()
        .map((item) => item.cast<String, dynamic>())
        .toList();
    final usedCount = metrics.where(_isUsed).length;
    final updateCount = metrics.where(_needsUpdate).length;
    final visible = switch (filter) {
      _EvidenceFilter.used => metrics.where(_isUsed).toList(),
      _EvidenceFilter.all => metrics,
      _EvidenceFilter.needsUpdate => metrics.where(_needsUpdate).toList(),
    };

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          Text(
            '分析日期 ${data!['analysis_date'] ?? '暂无'}',
            style: const TextStyle(fontSize: 12, color: Colors.black54),
          ),
          const SizedBox(height: 6),
          const Text(
            '先看本次判断实际参考的数据；其他指标用于确认数据是否齐全、是否需要更新。',
            style: TextStyle(fontSize: 13, height: 1.45, color: Colors.black87),
          ),
          const SizedBox(height: 8),
          const Text(
            '判断生成之后新到达的数据，会参与下一次分析（每天早晚各一档；'
            '也可在「今日」页点「立即重新分析」马上用最新数据重算）。',
            style: TextStyle(fontSize: 12, height: 1.5, color: Ed.inkSoft),
          ),
          const SizedBox(height: 16),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                _filterChip(
                  label: '本次使用 $usedCount',
                  value: _EvidenceFilter.used,
                ),
                const SizedBox(width: 8),
                _filterChip(
                  label: '全部 ${metrics.length}',
                  value: _EvidenceFilter.all,
                ),
                const SizedBox(width: 8),
                _filterChip(
                  label: '需更新 $updateCount',
                  value: _EvidenceFilter.needsUpdate,
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
          if (visible.isEmpty)
            const Card(
              child: Padding(
                padding: EdgeInsets.all(20),
                child: Text('当前筛选条件下没有可展示的健康指标。'),
              ),
            )
          else
            for (final metric in visible) _metricCard(context, metric),
        ],
      ),
    );
  }

  Widget _filterChip({required String label, required _EvidenceFilter value}) {
    return ChoiceChip(
      label: Text(label),
      selected: filter == value,
      onSelected: (_) => setState(() => filter = value),
      showCheckmark: false,
    );
  }

  Widget _metricCard(BuildContext context, Map<String, dynamic> metric) {
    final key = '${metric['key'] ?? ''}';
    final isExpanded = expanded.contains(key);
    final used = _isUsed(metric);
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
      margin: const EdgeInsets.only(bottom: 12),
      clipBehavior: Clip.antiAlias,
      child: Semantics(
        button: true,
        expanded: isExpanded,
        label:
            '${metric['label']}，${metric['value_display']}，${metric['freshness_label']}',
        child: InkWell(
          onTap: () => setState(() {
            if (isExpanded) {
              expanded.remove(key);
            } else {
              expanded.add(key);
            }
          }),
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
                        color: Theme.of(
                          context,
                        ).colorScheme.primary.withValues(alpha: 0.08),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Icon(
                        _metricIcon(key),
                        size: 21,
                        color: Theme.of(context).colorScheme.primary,
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
                    Icon(
                      isExpanded ? Icons.expand_less : Icons.expand_more,
                      color: Colors.black45,
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    if (used)
                      _statusChip(
                        '用于本次判断',
                        const Color(0xFFE8F0FF),
                        const Color(0xFF285EA8),
                      ),
                    _freshnessChip(status, metric),
                  ],
                ),
                const SizedBox(height: 9),
                Text(
                  used
                      ? '${metric['deviation_label'] ?? '已纳入本次判断'}'
                      : '仅作数据参考，不参与本次判断',
                  style: const TextStyle(fontSize: 12.5, color: Colors.black54),
                ),
                if (isExpanded) ...[
                  const Divider(height: 28),
                  Text(
                    '近期趋势（${_unitLabel('${metric['unit'] ?? ''}')}）',
                    style: const TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 8),
                  if (points.isEmpty)
                    Text(
                      '${metric['availability_note'] ?? '暂无足够的连续数据绘制趋势。'}',
                      style: const TextStyle(
                        fontSize: 12.5,
                        color: Colors.black54,
                      ),
                    )
                  else ...[
                    SizedBox(
                      height: 128,
                      child: TrendChart(
                        points: points,
                        baselineMedian: (metric['baseline_median'] as num?)
                            ?.toDouble(),
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      '${points.first.$1} — ${points.last.$1}',
                      style: const TextStyle(
                        fontSize: 11.5,
                        color: Colors.black54,
                      ),
                    ),
                  ],
                  if (metric['baseline_value_display'] != null) ...[
                    const SizedBox(height: 6),
                    Text(
                      '个人近期基线 ${metric['baseline_value_display']}',
                      style: const TextStyle(fontSize: 12.5),
                    ),
                  ],
                  const SizedBox(height: 6),
                  Text(
                    status == 'STALE'
                        ? '该数值不是今天的数据，不能据此判断今天是否正常。'
                        : '数据日期 ${metric['data_date'] ?? '暂无'}',
                    style: TextStyle(
                      fontSize: 11.5,
                      color: status == 'STALE'
                          ? const Color(0xFFB66A00)
                          : Colors.black54,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _freshnessChip(String status, Map<String, dynamic> metric) {
    return switch (status) {
      'TODAY' => _statusChip(
        '今日数据',
        const Color(0xFFEEF3F8),
        const Color(0xFF33557A),
      ),
      'RECENT' => _statusChip(
        '非当日 · ${_shortDate(metric['data_date'])}',
        const Color(0xFFEFF1F4),
        const Color(0xFF667085),
      ),
      'STALE' => _statusChip(
        '需更新 · ${_shortDate(metric['data_date'])}',
        const Color(0xFFFFF4DD),
        const Color(0xFFB66A00),
      ),
      _ => _statusChip(
        '暂无可用数据',
        const Color(0xFFEFF1F4),
        const Color(0xFF667085),
      ),
    };
  }

  Widget _statusChip(String label, Color background, Color foreground) {
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

  bool _isUsed(Map<String, dynamic> metric) =>
      metric['used_in_judgment'] == true;

  bool _needsUpdate(Map<String, dynamic> metric) =>
      metric['freshness_status'] == 'STALE' ||
      metric['freshness_status'] == 'UNAVAILABLE';

  String _shortDate(dynamic value) {
    final text = '$value';
    final parts = text.split('-');
    return parts.length == 3 ? '${parts[1]}/${parts[2]}' : text;
  }

  String _unitLabel(String unit) {
    return switch (unit) {
      'steps' => '步',
      'vendor_calories' => '设备记录值',
      'seconds' => '小时',
      'bpm' => '次/分',
      'percent' => '%',
      'vendor_score' => '设备原始指标',
      _ => '原始单位',
    };
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
