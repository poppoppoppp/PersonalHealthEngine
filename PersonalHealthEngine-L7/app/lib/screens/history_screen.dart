/// 历史 — organized by Health Episode (§37–§40): continuous related changes aggregated
/// into 开始 → 发展 → 变化 → 恢复/结束. Ordinary stable days are hidden by default but
/// never deleted. This is a projection of append-only L6 history.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../main.dart';
import '../widgets/api_error_view.dart';

class HistoryScreen extends StatefulWidget {
  final AppEnv env;
  const HistoryScreen({super.key, required this.env});
  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends State<HistoryScreen> {
  final searchController = TextEditingController();
  List<dynamic>? episodes;
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

  Color _phaseColor(String phase) => switch (phase) {
    'DEVELOPING' => const Color(0xFF4F5D9E),
    'RECOVERING' => const Color(0xFF4A6B57),
    _ => const Color(0xFF5B6B7A),
  };

  String _phaseLabel(String phase) => switch (phase) {
    'DEVELOPING' => '进行中',
    'RECOVERING' => '恢复中',
    _ => '已结束',
  };

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
    return Scaffold(
      appBar: AppBar(title: const Text('历史')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView.builder(
          padding: const EdgeInsets.all(16),
          itemCount: items.length + 2,
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
                  if (episodes != null && episodes!.isEmpty)
                    const Card(
                      child: Padding(
                        padding: EdgeInsets.all(20),
                        child: Text(
                          '还没有形成任何健康事件。稳定日默认不显示，但完整保存。',
                          style: TextStyle(color: Colors.black54, height: 1.5),
                        ),
                      ),
                    ),
                ],
              );
            }
            if (index <= items.length) return _episodeCard(items[index - 1]);
            return Column(
              children: [
                if (stableHidden > 0)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(
                      '另有 $stableHidden 个稳定日未显示（完整保存）。${note ?? ''}',
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
                      label: const Text('加载更早事件'),
                    ),
                  ),
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _episodeCard(Map<String, dynamic> episode) {
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
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
                      '${episode['start_date']} ~ ${episode['end_date'] ?? episode['start_date']}',
                      style: const TextStyle(
                        fontWeight: FontWeight.w600,
                        fontSize: 14,
                      ),
                    ),
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 3,
                    ),
                    decoration: BoxDecoration(
                      color: _phaseColor(
                        '${episode['phase']}',
                      ).withOpacity(0.12),
                      borderRadius: BorderRadius.circular(999),
                    ),
                    child: Text(
                      _phaseLabel('${episode['phase']}'),
                      style: TextStyle(
                        fontSize: 11,
                        color: _phaseColor('${episode['phase']}'),
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                '${episode['summary'] ?? ''}',
                style: const TextStyle(
                  fontSize: 13,
                  height: 1.5,
                  color: Colors.black87,
                ),
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
      appBar: AppBar(title: const Text('事件时间线')),
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
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            '${ep['summary'] ?? ''}',
                            style: const TextStyle(fontSize: 14, height: 1.5),
                          ),
                        ],
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
                                '${ev['event_date']} · ${ev['kind_label'] ?? '事件记录'}',
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
                      label: const Text('加载更早记录'),
                    ),
                  ),
              ],
            ),
    );
  }

  String _detailText(dynamic detail) {
    if (detail is! Map) return '$detail';
    final m = detail.cast<String, dynamic>();
    if (m.containsKey('overall_state')) {
      return '判断：${m['overall_state_label'] ?? '状态有变化'}，'
          '主因 ${m['primary_label'] ?? '暂无法确定原因'}'
          '${m['version_status_label'] == '历史版本' ? '（已被更新版本取代）' : ''}';
    }
    if (m.containsKey('context_type')) {
      return '情况：${m['context_type_label'] ?? '其他个人情况'}'
          '${m['raw_text'] != null ? ' · ${m['raw_text']}' : ''}';
    }
    if (m.containsKey('feedback_status')) {
      return '反馈：${m['feedback_status_label'] ?? '已记录反馈'}'
          '${m['correction'] == true ? '（含纠正）' : ''}';
    }
    return '$detail';
  }
}
