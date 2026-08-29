/// 今日 — Today-first / Conclusion-first screen.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../api_client.dart';
import '../main.dart';
import '../widgets/api_error_view.dart';
import 'context_screen.dart';
import 'evidence_screen.dart';
import 'qa_screen.dart';

const Map<String, Color> _stateColors = {
  'A': Color(0xFF4A6B57),
  'B': Color(0xFF5B6B7A),
  'C': Color(0xFF4F5D9E),
  'D': Color(0xFF7A7466),
  'E': Color(0xFF9E3B3B),
};

class TodayScreen extends StatefulWidget {
  final AppEnv env;
  const TodayScreen({super.key, required this.env});
  @override
  State<TodayScreen> createState() => _TodayScreenState();
}

class _TodayScreenState extends State<TodayScreen> {
  TodayPayload? today;
  Object? error;
  bool loading = false;
  bool fromCache = false;

  @override
  void initState() {
    super.initState();
    widget.env.addListener(_onDataChanged);
    _boot();
  }

  @override
  void dispose() {
    widget.env.removeListener(_onDataChanged);
    super.dispose();
  }

  void _onDataChanged() => unawaited(_fetch());

  Future<void> _boot() async {
    final repository = await widget.env.repository();
    final cached = await repository.cached('today');
    if (cached != null && mounted) {
      setState(() {
        today = TodayPayload(cached);
        fromCache = true;
      });
    }
    await _fetch();
  }

  Future<void> _fetch() async {
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final repository = await widget.env.repository();
      final raw = await repository.refreshUsing(
        'today',
        client: widget.env.client,
        path: '/today',
        fallback: () async => (await widget.env.client.getToday()).raw,
        versionOf: (data) => data['version_id'] as int? ?? 0,
      );
      if (raw == null) throw const FormatException('missing Today payload');
      final p = TodayPayload(raw);
      if (mounted) {
        setState(() {
          today = p;
          fromCache = false;
          loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          loading = false;
          error = e;
        });
      }
    }
  }

  Future<void> _refresh() async {
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final r = await widget.env.client.refreshToday();
      final p = TodayPayload((r['today'] as Map).cast<String, dynamic>());
      final repository = await widget.env.repository();
      await repository.cache.store('today', p.raw, version: p.versionId ?? 0);
      if (mounted) {
        setState(() {
          today = p;
          fromCache = false;
          loading = false;
          error = null;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          loading = false;
          error = e;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = today;
    return Scaffold(
      appBar: AppBar(
        title: const Text('今日'),
        centerTitle: false,
        actions: [
          if (p != null)
            Padding(
              padding: const EdgeInsets.only(right: 16),
              child: Center(
                child: Text(
                  '更新于 ${p.updatedAtLocal}',
                  style: Theme.of(
                    context,
                  ).textTheme.bodySmall?.copyWith(color: Colors.black54),
                ),
              ),
            ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
          children: [
            if (fromCache && loading)
              const Padding(
                padding: EdgeInsets.only(bottom: 8),
                child: Row(
                  children: [
                    SizedBox(
                      width: 14,
                      height: 14,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                    SizedBox(width: 8),
                    Text(
                      '正在检查最新证据…',
                      style: TextStyle(color: Colors.black54, fontSize: 12),
                    ),
                  ],
                ),
              ),
            if (error != null && p == null) _errorCard(context),
            if (error != null && p != null) ...[
              ApiErrorView(error: error!, onRetry: _refresh),
              const SizedBox(height: 8),
            ],
            if (p != null) ..._todayBody(context, p),
            if (p == null && error == null && loading)
              const Padding(
                padding: EdgeInsets.only(top: 80),
                child: Center(child: CircularProgressIndicator()),
              ),
          ],
        ),
      ),
    );
  }

  Widget _errorCard(BuildContext context) {
    return ApiErrorView(error: error!, onRetry: _fetch);
  }

  List<Widget> _todayBody(BuildContext context, TodayPayload p) {
    final color = _stateColors[p.productState] ?? const Color(0xFF5B6B7A);
    final blocks = <String, Widget>{
      'cause': _causeBlock(context, p),
      'action': _actionsBlock(context, p),
    };
    final ordered = <Widget>[];
    for (final key in p.informationOrder) {
      if (key == 'cause') ordered.add(blocks['cause']!);
      if (key == 'action') ordered.add(blocks['action']!);
      // 'conclusion' is the header card itself, rendered first regardless.
    }

    return [
      if (p.judgmentUpdated)
        Container(
          margin: const EdgeInsets.only(bottom: 8),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            color: color.withOpacity(0.08),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Row(
            children: [
              Icon(Icons.published_with_changes, size: 16, color: color),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  p.changeNote ?? '判断已更新',
                  style: TextStyle(fontSize: 13, color: color),
                ),
              ),
              TextButton(
                onPressed: () => _showVersionHistory(context),
                child: const Text('查看变化来源'),
              ),
            ],
          ),
        ),
      // Conclusion-first header card.
      Container(
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: color.withOpacity(0.25)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 6,
                  ),
                  decoration: BoxDecoration(
                    color: color.withOpacity(0.1),
                    borderRadius: BorderRadius.circular(999),
                  ),
                  child: Text(
                    p.productStateLabel,
                    style: TextStyle(
                      color: color,
                      fontWeight: FontWeight.w700,
                      fontSize: 14,
                    ),
                  ),
                ),
                const Spacer(),
                Text(
                  '置信度：${p.confidenceLabel}',
                  style: const TextStyle(fontSize: 12, color: Colors.black45),
                ),
              ],
            ),
            const SizedBox(height: 14),
            Text(
              p.headline,
              style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
            ),
            if (p.dataAsOf != null) ...[
              const SizedBox(height: 6),
              Text(
                '基于截至 ${p.dataAsOf} 的全部已知信息',
                style: const TextStyle(fontSize: 12, color: Colors.black45),
              ),
            ],
          ],
        ),
      ),
      const SizedBox(height: 12),
      ...ordered,
      const SizedBox(height: 12),
      _evidenceBlock(context, p),
      const SizedBox(height: 12),
      _entryRow(context),
      if (p.feedbackPrompt != null) ...[
        const SizedBox(height: 12),
        _feedbackBlock(context, p),
      ],
    ];
  }

  Widget _causeBlock(BuildContext context, TodayPayload p) {
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              '最可能原因',
              style: TextStyle(
                fontSize: 13,
                color: Colors.black54,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              p.causeText.isEmpty ? '（暂无）' : p.causeText,
              style: const TextStyle(fontSize: 15, height: 1.5),
            ),
            if (p.secondaryCause != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  '次要可能：${p.secondaryCause!['hypothesis_label'] ?? '暂无法确定原因'}',
                  style: const TextStyle(fontSize: 12, color: Colors.black45),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _actionsBlock(BuildContext context, TodayPayload p) {
    if (p.actions.isEmpty) {
      return const SizedBox.shrink(); // stable day: 0 actions is allowed (§15)
    }
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              '今日行动',
              style: TextStyle(
                fontSize: 13,
                color: Colors.black54,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: 8),
            for (final a in p.actions)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(
                      Icons.check_circle_outline,
                      size: 18,
                      color: Colors.black45,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(a, style: const TextStyle(height: 1.4)),
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _evidenceBlock(BuildContext context, TodayPayload p) {
    final factsByFeature = <String, Map<String, dynamic>>{};
    for (final fact in p.evidence) {
      final feature = '${fact['feature_name'] ?? ''}';
      if (feature.contains('bucket_count')) continue;
      final key = feature.isNotEmpty ? feature : _evidenceLabel(fact);
      factsByFeature.putIfAbsent(key, () => fact);
    }
    final allFacts = factsByFeature.values.toList();
    final facts = allFacts.take(2).toList();
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute(builder: (_) => EvidenceScreen(env: widget.env)),
        ),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      facts.isEmpty
                          ? '判断依据'
                          : '本次判断参考了 ${allFacts.length} 项健康数据',
                      style: TextStyle(
                        fontSize: 14,
                        color: Colors.black87,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    '全部指标',
                    style: TextStyle(
                      fontSize: 12.5,
                      color: Theme.of(context).colorScheme.primary,
                    ),
                  ),
                  const Icon(Icons.chevron_right, size: 18),
                ],
              ),
              const SizedBox(height: 12),
              if (facts.isEmpty)
                const Text(
                  '本次判断只参考了数据完整性，未使用可解释的健康指标。可在全部指标中查看步数、睡眠、心率等数据及日期。',
                  style: TextStyle(color: Colors.black54, height: 1.45),
                )
              else
                for (final fact in facts)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 6),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.center,
                      children: [
                        Container(
                          width: 34,
                          height: 34,
                          decoration: BoxDecoration(
                            color: Theme.of(
                              context,
                            ).colorScheme.primary.withValues(alpha: 0.08),
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: Icon(
                            _evidenceIcon(fact),
                            size: 18,
                            color: Theme.of(context).colorScheme.primary,
                          ),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Row(
                                children: [
                                  Expanded(
                                    child: Text(
                                      _evidenceLabel(fact),
                                      style: const TextStyle(
                                        fontSize: 14,
                                        fontWeight: FontWeight.w600,
                                      ),
                                    ),
                                  ),
                                  Text(
                                    '${fact['current_value_display'] ?? '暂无数据'}',
                                    style: const TextStyle(
                                      fontSize: 14,
                                      fontWeight: FontWeight.w700,
                                    ),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 3),
                              Wrap(
                                spacing: 8,
                                runSpacing: 2,
                                children: [
                                  Text(
                                    '${fact['freshness_label'] ?? fact['feature_date'] ?? '日期未知'}',
                                    style: const TextStyle(
                                      fontSize: 11.5,
                                      color: Colors.black54,
                                    ),
                                  ),
                                  Text(
                                    '${fact['deviation_label'] ?? '用于本次判断'}',
                                    style: TextStyle(
                                      fontSize: 11.5,
                                      color: Theme.of(
                                        context,
                                      ).colorScheme.primary,
                                      fontWeight: FontWeight.w600,
                                    ),
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
            ],
          ),
        ),
      ),
    );
  }

  String _evidenceLabel(Map<String, dynamic> fact) {
    final metric = '${fact['metric'] ?? ''}';
    final feature = '${fact['feature_name'] ?? ''}';
    if (metric == 'steps' || feature.startsWith('steps.')) return '步数';
    if (metric == 'calories' || feature.startsWith('calories.')) {
      return '活动消耗';
    }
    if (metric == 'sleep' || feature.startsWith('sleep_source_episode.')) {
      return '睡眠';
    }
    if (metric == 'resting_heart_rate' ||
        feature.startsWith('resting_heart_rate.')) {
      return '静息心率';
    }
    if (metric == 'heart_rate' || feature.startsWith('heart_rate.')) {
      return '心率';
    }
    if (metric == 'spo2' || feature.startsWith('spo2.')) return '血氧';
    if (metric == 'xiaomi_stress_score' ||
        feature.startsWith('xiaomi_stress_score.')) {
      return '压力';
    }
    return switch (metric) {
      'steps' => '步数',
      'calories' => '活动消耗',
      'sleep' => '睡眠',
      'heart_rate' => '心率',
      'resting_heart_rate' => '静息心率',
      'spo2' => '血氧',
      'xiaomi_stress_score' => '压力',
      _ => '${fact['feature_label'] ?? fact['metric_label'] ?? '健康指标'}',
    };
  }

  IconData _evidenceIcon(Map<String, dynamic> fact) {
    final metric = '${fact['metric'] ?? ''}';
    final feature = '${fact['feature_name'] ?? ''}';
    if (feature.startsWith('steps.')) return Icons.directions_walk;
    if (feature.startsWith('calories.')) {
      return Icons.local_fire_department_outlined;
    }
    if (feature.startsWith('sleep_source_episode.')) {
      return Icons.bedtime_outlined;
    }
    if (feature.startsWith('resting_heart_rate.') ||
        feature.startsWith('heart_rate.')) {
      return Icons.favorite_border;
    }
    if (feature.startsWith('spo2.')) return Icons.water_drop_outlined;
    if (feature.startsWith('xiaomi_stress_score.')) {
      return Icons.self_improvement;
    }
    return switch (metric) {
      'steps' => Icons.directions_walk,
      'calories' => Icons.local_fire_department_outlined,
      'sleep' => Icons.bedtime_outlined,
      'heart_rate' || 'resting_heart_rate' => Icons.favorite_border,
      'spo2' => Icons.water_drop_outlined,
      'xiaomi_stress_score' => Icons.self_improvement,
      _ => Icons.monitor_heart_outlined,
    };
  }

  Widget _entryRow(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: OutlinedButton.icon(
            onPressed: () => Navigator.of(context)
                .push(
                  MaterialPageRoute(builder: (_) => QnAScreen(env: widget.env)),
                )
                .then((_) => _fetch()),
            icon: const Icon(Icons.chat_bubble_outline, size: 18),
            label: const Padding(
              padding: EdgeInsets.symmetric(vertical: 10),
              child: Text('问问我的状态'),
            ),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: OutlinedButton.icon(
            onPressed: () => Navigator.of(context)
                .push(
                  MaterialPageRoute(
                    builder: (_) => ContextScreen(env: widget.env),
                  ),
                )
                .then((_) => _fetch()),
            icon: const Icon(Icons.add_comment_outlined, size: 18),
            label: const Padding(
              padding: EdgeInsets.symmetric(vertical: 10),
              child: Text('补充我的情况'),
            ),
          ),
        ),
      ],
    );
  }

  Widget _feedbackBlock(BuildContext context, TodayPayload p) {
    final fp = p.feedbackPrompt!;
    final options = (fp['options'] as List? ?? const []).map((e) => '$e');
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '${fp['prompt'] ?? '这个判断准确吗？'}',
              style: const TextStyle(fontSize: 13, color: Colors.black54),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              children: [
                for (final o in options)
                  ActionChip(
                    label: Text(o),
                    onPressed: _feedbackBusy ? null : () => _sendFeedback(o),
                  ),
              ],
            ),
            if (_feedbackBusy)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Row(
                  children: [
                    SizedBox(
                      width: 12,
                      height: 12,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                    SizedBox(width: 8),
                    Text(
                      '正在保存反馈…',
                      style: TextStyle(fontSize: 12, color: Colors.black45),
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }

  bool _feedbackBusy = false;
  String? _feedbackRetryKey;
  String? _feedbackRetryPayload;

  Future<void> _sendFeedback(String verdict) async {
    String? text;
    if (verdict == '补充情况') {
      text = await _askSupplementText();
      if (text == null || text.trim().isEmpty) return;
    }
    setState(() => _feedbackBusy = true);
    final payloadKey = '$verdict\n${text ?? ''}';
    final idempotencyKey =
        _feedbackRetryPayload == payloadKey && _feedbackRetryKey != null
        ? _feedbackRetryKey!
        : 'feedback-${DateTime.now().microsecondsSinceEpoch}';
    _feedbackRetryPayload = payloadKey;
    _feedbackRetryKey = idempotencyKey;
    try {
      final r = await widget.env.client.submitFeedback(
        verdict,
        text: text,
        idempotencyKey: idempotencyKey,
      );
      _feedbackRetryPayload = null;
      _feedbackRetryKey = null;
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('反馈已保存，正在后台复核今日判断。')));
      }
      final jobId = r['job_id'] as int?;
      if (jobId != null) unawaited(_finishFeedbackJob(jobId));
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('反馈提交失败：${apiErrorMessage(e)}')));
      }
    } finally {
      if (mounted) setState(() => _feedbackBusy = false);
    }
  }

  Future<void> _finishFeedbackJob(int jobId) async {
    var delay = const Duration(milliseconds: 500);
    for (var attempt = 0; attempt < 6; attempt++) {
      await Future<void>.delayed(delay);
      try {
        final status = await widget.env.client.getJobStatus(jobId);
        if (status['status'] == 'SUCCEEDED') {
          final repository = await widget.env.repository();
          await repository.cache.invalidate('today');
          await repository.cache.invalidate('history');
          await repository.cache.invalidate('patterns');
          widget.env.notifyDataChanged();
          if (mounted) {
            ScaffoldMessenger.of(
              context,
            ).showSnackBar(const SnackBar(content: Text('后台复核已完成，今日判断已刷新。')));
          }
          return;
        }
        if (status['status'] == 'FAILED') {
          throw const ApiException(ApiErrorKind.server, '后台复核失败，请重新提交反馈');
        }
      } catch (e) {
        if (attempt == 5 && mounted) {
          ScaffoldMessenger.of(
            context,
          ).showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
        }
      }
      delay = Duration(
        milliseconds: (delay.inMilliseconds * 2).clamp(500, 4000).toInt(),
      );
    }
  }

  Future<String?> _askSupplementText() {
    final controller = TextEditingController();
    return showDialog<String>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('补充什么情况？'),
        content: TextField(
          controller: controller,
          maxLines: 3,
          autofocus: true,
          decoration: const InputDecoration(hintText: '例如：其实昨晚喝了酒 / 最近工作压力很大'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text),
            child: const Text('提交'),
          ),
        ],
      ),
    );
  }

  void _showVersionHistory(BuildContext context) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (_) => _VersionHistorySheet(env: widget.env),
    );
  }
}

class _VersionHistorySheet extends StatefulWidget {
  final AppEnv env;
  const _VersionHistorySheet({required this.env});
  @override
  State<_VersionHistorySheet> createState() => _VersionHistorySheetState();
}

class _VersionHistorySheetState extends State<_VersionHistorySheet> {
  List<dynamic>? versions;
  Object? error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => error = null);
    try {
      final result = await widget.env.client.getTodayVersions();
      if (mounted) setState(() => versions = result['versions'] as List);
    } catch (e) {
      if (mounted) setState(() => error = e);
    }
  }

  @override
  Widget build(BuildContext context) {
    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.5,
      builder: (_, scroll) => Column(
        children: [
          const Padding(
            padding: EdgeInsets.all(16),
            child: Text(
              '判断版本历史（旧判断不会被删除）',
              style: TextStyle(fontWeight: FontWeight.w600),
            ),
          ),
          Expanded(
            child: error != null
                ? Center(
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: ApiErrorView(error: error!, onRetry: _load),
                    ),
                  )
                : versions == null
                ? const Center(child: CircularProgressIndicator())
                : ListView.builder(
                    controller: scroll,
                    itemCount: versions!.length,
                    itemBuilder: (_, i) {
                      final v = (versions![i] as Map).cast<String, dynamic>();
                      return ListTile(
                        leading: const Icon(Icons.history, size: 20),
                        title: Text(
                          '${v['analysis_date']} · ${v['product_state_label'] ?? '状态未知'}',
                        ),
                        subtitle: Text(
                          '${v['created_at_local'] ?? ''} · ${v['trigger_label'] ?? '系统更新'}'
                          '${(v['judgment_updated'] == 1) ? ' · 判断已更新' : ''}',
                        ),
                        isThreeLine: false,
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}
