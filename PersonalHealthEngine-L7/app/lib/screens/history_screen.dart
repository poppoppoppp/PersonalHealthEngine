/// 历史 — 两个问题：这段时间我的数据长什么样（数据回看），以及引擎记下了哪些变化
/// （身体变化记录，按 Health Episode 投影，§37–§40）。稳定日默认隐藏但完整保存。
/// 界面只讲人话：数值、图表、白话摘要；不出现置信度、枚举标签或版本管理词汇。
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../main.dart';
import '../widgets/api_error_view.dart';
import '../widgets/metric_overview_card.dart';
import '../widgets/sleep_structure_card.dart';

class HistoryScreen extends StatefulWidget {
  final AppEnv env;
  const HistoryScreen({super.key, required this.env});
  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends State<HistoryScreen> {
  final searchController = TextEditingController();
  List<dynamic>? episodes;
  List<Map<String, dynamic>>? metrics;
  String selectedMetricKey = '';
  Map<String, dynamic>? sleepStructure;
  int stableHidden = 0;
  String? note;
  Object? error;
  bool searching = false;
  bool loading = false;
  bool loadingMore = false;
  int? nextCursor;

  @override
  void initState() {
    super.initState();
    widget.env.addListener(_onDataChanged);
    _load();
  }

  void _onDataChanged() => unawaited(_load());

  Future<void> _load() async {
    unawaited(_loadMetrics());
    final repository = await widget.env.repository();
    final cached = await repository.cached('history');
    if (cached != null && mounted && episodes == null) {
      _apply(cached);
    }
    if (mounted) setState(() => loading = true);
    try {
      final r = await repository.refreshUsing(
        'history',
        client: widget.env.client,
        path: '/history/episodes?limit=30',
        fallback: widget.env.client.getEpisodes,
        versionOf: (data) => data['projection_version'] as int? ?? 1,
      );
      if (mounted) {
        if (r != null) _apply(r);
        setState(() => loading = false);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          error = e;
          loading = false;
        });
      }
    }
  }

  /// 指标回看与事件记录互不阻塞：指标加载失败只隐藏回看区，不影响事件列表。
  Future<void> _loadMetrics() async {
    try {
      final r = await widget.env.client.getEvidence();
      if (!mounted) return;
      final all = (r['all_metrics'] as List? ?? const [])
          .whereType<Map>()
          .map((item) => item.cast<String, dynamic>())
          .where((m) => (m['series'] as List? ?? const []).isNotEmpty)
          .toList();
      setState(() {
        metrics = all;
        if (all.isNotEmpty &&
            !all.any((m) => '${m['key'] ?? ''}' == selectedMetricKey)) {
          selectedMetricKey =
              all.any((m) => '${m['key'] ?? ''}' == 'sleep')
              ? 'sleep'
              : '${all.first['key'] ?? ''}';
        }
      });
      if (selectedMetricKey == 'sleep') {
        unawaited(_loadSleepStructure());
      }
    } catch (_) {
      if (mounted) setState(() => metrics = const []);
    }
  }

  Future<void> _loadSleepStructure() async {
    if (sleepStructure != null) return;
    try {
      final r = await widget.env.client.getSleepStructure();
      if (mounted) setState(() => sleepStructure = r);
    } catch (_) {
      if (mounted) setState(() => sleepStructure = {'nights': const []});
    }
  }

  void _apply(Map<String, dynamic> r, {bool append = false}) {
    setState(() {
      final page = r['episodes'] as List? ?? const [];
      episodes = append ? [...?episodes, ...page] : page;
      nextCursor = r['next_cursor'] as int?;
      stableHidden = r['stable_days_hidden'] as int? ?? 0;
      note = r['note'] as String?;
      error = null;
    });
  }

  Future<void> _loadMore() async {
    final cursor = nextCursor;
    if (cursor == null || loadingMore) return;
    setState(() => loadingMore = true);
    try {
      final page = await widget.env.client.getEpisodes(cursor: cursor);
      if (mounted) _apply(page, append: true);
    } catch (e) {
      if (mounted) setState(() => error = e);
    } finally {
      if (mounted) setState(() => loadingMore = false);
    }
  }

  Future<void> _search() async {
    final q = searchController.text.trim();
    if (q.isEmpty) {
      _load();
      return;
    }
    setState(() => searching = true);
    try {
      final r = await widget.env.client.searchHistory(q);
      if (mounted) {
        setState(() {
          episodes = r['results'] as List;
          searching = false;
          note = null;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          searching = false;
          error = e;
        });
      }
    }
  }

  @override
  void dispose() {
    widget.env.removeListener(_onDataChanged);
    searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final items = (episodes ?? const [])
        .map((x) => (x as Map).cast<String, dynamic>())
        .toList();
    final metricList = metrics ?? const [];
    final selected = metricList
        .where((m) => '${m['key'] ?? ''}' == selectedMetricKey)
        .toList();

    return Scaffold(
      appBar: AppBar(title: const Text('历史')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView.builder(
          padding: const EdgeInsets.all(16),
          itemCount: items.length + 5,
          itemBuilder: (context, index) {
            if (index == 0) {
              return Column(
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: TextField(
                          controller: searchController,
                          decoration: const InputDecoration(
                            hintText: '搜索（如：睡眠、喝酒）',
                            isDense: true,
                            prefixIcon: Icon(Icons.search, size: 18),
                            border: OutlineInputBorder(),
                          ),
                          onSubmitted: (_) => _search(),
                        ),
                      ),
                      const SizedBox(width: 8),
                      IconButton.filledTonal(
                        onPressed: searching ? null : _search,
                        icon: const Icon(Icons.search),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  if (loading && episodes != null)
                    const LinearProgressIndicator(minHeight: 2),
                  if (error != null)
                    ApiErrorView(error: error!, onRetry: _load),
                  if (episodes == null && error == null)
                    const Padding(
                      padding: EdgeInsets.only(top: 40),
                      child: Center(child: CircularProgressIndicator()),
                    ),
                ],
              );
            }
            // 数据回看：指标切换 + 单指标趋势卡。
            if (index == 1) {
              if (metricList.isEmpty) return const SizedBox.shrink();
              return _metricExplorer(metricList, selected);
            }
            if (index == 2) {
              if (episodes == null) return const SizedBox.shrink();
              return Padding(
                padding: const EdgeInsets.only(top: 20, bottom: 4),
                child: Text(
                  items.isEmpty ? '' : '身体变化记录',
                  style: const TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              );
            }
            if (index == 3) {
              if (episodes == null || items.isNotEmpty) {
                return const SizedBox.shrink();
              }
              return const Card(
                child: Padding(
                  padding: EdgeInsets.all(20),
                  child: Text(
                    '这段时间没有记录到需要你注意的变化。\n数据都在，随时可以在上面回看。',
                    style: TextStyle(color: Colors.black54, height: 1.5),
                  ),
                ),
              );
            }
            final episodeIndex = index - 4;
            if (episodeIndex < items.length) {
              return _episodeCard(items[episodeIndex]);
            }
            return Column(
              children: [
                if (stableHidden > 0)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(
                      '其余 $stableHidden 天状态平稳，没有需要你处理的变化。',
                      style: const TextStyle(
                        fontSize: 12,
                        color: Colors.black45,
                      ),
                    ),
                  ),
                if (nextCursor != null)
                  Center(
                    child: TextButton.icon(
                      onPressed: loadingMore ? null : _loadMore,
                      icon: loadingMore
                          ? const SizedBox(
                              width: 16,
                              height: 16,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.expand_more),
                      label: const Text('加载更早的记录'),
                    ),
                  ),
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _metricExplorer(
    List<Map<String, dynamic>> metricList,
    List<Map<String, dynamic>> selected,
  ) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Padding(
          padding: EdgeInsets.only(bottom: 8),
          child: Text(
            '数据回看',
            style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
          ),
        ),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Row(
            children: [
              for (final m in metricList)
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    label: Text('${m['label'] ?? m['key']}'),
                    selected: '${m['key'] ?? ''}' == selectedMetricKey,
                    onSelected: (_) {
                      setState(() => selectedMetricKey = '${m['key'] ?? ''}');
                      if (selectedMetricKey == 'sleep') {
                        unawaited(_loadSleepStructure());
                      }
                    },
                    showCheckmark: false,
                  ),
                ),
            ],
          ),
        ),
        const SizedBox(height: 8),
        if (selected.isNotEmpty) MetricOverviewCard(metric: selected.first),
        if (selectedMetricKey == 'sleep' && sleepStructure != null)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: SleepStructureCard(
              nights: (sleepStructure!['nights'] as List? ?? const [])
                  .whereType<Map>()
                  .map((item) => item.cast<String, dynamic>())
                  .toList(),
            ),
          ),
      ],
    );
  }

  Widget _episodeCard(Map<String, dynamic> episode) {
    final developing = '${episode['phase']}' == 'DEVELOPING';
    return Card(
      margin: const EdgeInsets.only(top: 10),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: episode['id'] == null
            ? null
            : () => _openEpisode(episode['id'] as int),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      '${episode['summary'] ?? ''}',
                      style: const TextStyle(
                        fontWeight: FontWeight.w600,
                        fontSize: 14,
                        height: 1.5,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 3,
                    ),
                    decoration: BoxDecoration(
                      color: (developing
                              ? const Color(0xFF4F5D9E)
                              : const Color(0xFF5B6B7A))
                          .withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(999),
                    ),
                    child: Text(
                      developing ? '进行中' : '已结束',
                      style: TextStyle(
                        fontSize: 11,
                        color: developing
                            ? const Color(0xFF4F5D9E)
                            : const Color(0xFF5B6B7A),
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _openEpisode(int id) async {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => _EpisodeDetailScreen(env: widget.env, episodeId: id),
      ),
    );
  }
}

class _EpisodeDetailScreen extends StatefulWidget {
  final AppEnv env;
  final int episodeId;
  const _EpisodeDetailScreen({required this.env, required this.episodeId});
  @override
  State<_EpisodeDetailScreen> createState() => _EpisodeDetailScreenState();
}

class _EpisodeDetailScreenState extends State<_EpisodeDetailScreen> {
  Map<String, dynamic>? data;
  Object? error;
  bool loading = false;
  bool loadingMore = false;
  int? nextCursor;

  @override
  void initState() {
    super.initState();
    widget.env.addListener(_onDataChanged);
    _load();
  }

  void _onDataChanged() => unawaited(_load());

  Future<void> _load() async {
    final repository = await widget.env.repository();
    final resource = 'timeline.${widget.episodeId}';
    final cached = await repository.cached(resource);
    if (cached != null && mounted && data == null) {
      setState(() => data = cached);
    }
    if (mounted) setState(() => loading = true);
    try {
      final fresh = await repository.refreshUsing(
        resource,
        client: widget.env.client,
        path: '/history/episodes/${widget.episodeId}?limit=30',
        fallback: () => widget.env.client.getEpisode(widget.episodeId),
        versionOf: (_) => DateTime.now().millisecondsSinceEpoch,
      );
      if (mounted) {
        setState(() {
          data = fresh ?? data;
          nextCursor = fresh?['next_cursor'] as int?;
          error = null;
          loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          error = e;
          loading = false;
        });
      }
    }
  }

  Future<void> _loadMore() async {
    final cursor = nextCursor;
    if (cursor == null || loadingMore) return;
    setState(() => loadingMore = true);
    try {
      final page = await widget.env.client.getEpisode(
        widget.episodeId,
        cursor: cursor,
      );
      if (mounted) {
        final current = data?['timeline'] as List? ?? const [];
        final older = page['timeline'] as List? ?? const [];
        setState(() {
          data = {
            ...?data,
            'timeline': [...current, ...older],
          };
          nextCursor = page['next_cursor'] as int?;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = e);
    } finally {
      if (mounted) setState(() => loadingMore = false);
    }
  }

  @override
  void dispose() {
    widget.env.removeListener(_onDataChanged);
    super.dispose();
  }

  IconData _kindIcon(String kind) => switch (kind) {
    'JUDGMENT' => Icons.psychology_outlined,
    'CONTEXT' => Icons.event_note_outlined,
    'FEEDBACK' => Icons.feedback_outlined,
    _ => Icons.circle_outlined,
  };

  @override
  Widget build(BuildContext context) {
    final ep = (data?['episode'] as Map?)?.cast<String, dynamic>();
    final timeline = (data?['timeline'] as List? ?? const [])
        .map((e) => (e as Map).cast<String, dynamic>())
        .toList();
    return Scaffold(
      appBar: AppBar(title: const Text('这段时间的记录')),
      body: error != null && data == null
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
                if (loading) const LinearProgressIndicator(minHeight: 2),
                if (error != null) ApiErrorView(error: error!, onRetry: _load),
                if (ep != null)
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: Text(
                        '${ep['summary'] ?? ''}',
                        style: const TextStyle(fontSize: 14, height: 1.5),
                      ),
                    ),
                  ),
                const SizedBox(height: 8),
                for (final ev in timeline)
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Column(
                        children: [
                          Icon(
                            _kindIcon('${ev['kind']}'),
                            size: 18,
                            color: Colors.black45,
                          ),
                        ],
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Container(
                          margin: const EdgeInsets.only(bottom: 10),
                          padding: const EdgeInsets.all(12),
                          decoration: BoxDecoration(
                            color: Colors.white,
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(color: Colors.black12),
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                '${_md('${ev['event_date'] ?? ''}')} · ${ev['kind_label'] ?? '记录'}',
                                style: const TextStyle(
                                  fontSize: 11,
                                  color: Colors.black45,
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                _detailText(ev['detail']),
                                style: const TextStyle(
                                  fontSize: 13,
                                  height: 1.5,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ],
                  ),
                if (nextCursor != null)
                  Center(
                    child: TextButton.icon(
                      onPressed: loadingMore ? null : _loadMore,
                      icon: const Icon(Icons.expand_more),
                      label: const Text('加载更早的记录'),
                    ),
                  ),
              ],
            ),
    );
  }

  String _md(String isoDate) {
    final parts = isoDate.split('-');
    if (parts.length != 3) return isoDate;
    final month = int.tryParse(parts[1]);
    final day = int.tryParse(parts[2]);
    if (month == null || day == null) return isoDate;
    return '$month月$day日';
  }

  String _detailText(dynamic detail) {
    if (detail is! Map) return '$detail';
    final m = detail.cast<String, dynamic>();
    if (m.containsKey('overall_state')) {
      final primary = '${m['primary'] ?? 'UNKNOWN'}';
      final cause = primary == 'UNKNOWN'
          ? '具体原因还不明确，继续观察。'
          : '主要线索：${m['primary_label'] ?? '待确认'}。';
      return '当天判断：${m['overall_state_label'] ?? '身体变化比较明显'}。$cause';
    }
    if (m.containsKey('context_type')) {
      return '你补充的情况：${m['context_type_label'] ?? '其他个人情况'}'
          '${m['raw_text'] != null ? ' · ${m['raw_text']}' : ''}';
    }
    if (m.containsKey('feedback_status')) {
      return '你的反馈：${m['feedback_status_label'] ?? '已记录'}'
          '${m['correction'] == true ? '（含补充）' : ''}';
    }
    return '$detail';
  }
}
