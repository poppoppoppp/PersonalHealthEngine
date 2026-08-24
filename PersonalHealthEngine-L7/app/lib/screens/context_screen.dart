/// Context capture (§23–§28). Natural language first, quick chips second, never a daily
/// questionnaire. Auto-save without confirmation dialogs; corrections supersede old facts
/// with full provenance.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../api_client.dart';
import '../main.dart';
import '../widgets/api_error_view.dart';

class ContextScreen extends StatefulWidget {
  final AppEnv env;
  const ContextScreen({super.key, required this.env});
  @override
  State<ContextScreen> createState() => _ContextScreenState();
}

class _ContextScreenState extends State<ContextScreen> {
  final controller = TextEditingController();
  List<Map<String, dynamic>> contexts = [];
  bool loadingList = true;
  bool submitting = false;
  Object? listError;
  bool loadingMore = false;
  int? nextCursor;
  String? _pendingText;
  String? _pendingKey;

  static const quickChips = ['熬夜', '高强度训练', '喝酒', '身体不舒服', '压力大'];

  @override
  void initState() {
    super.initState();
    widget.env.addListener(_onDataChanged);
    _loadList();
  }

  Future<void> _loadList() async {
    final repository = await widget.env.repository();
    final cached = await repository.cached('context');
    if (cached != null && mounted && contexts.isEmpty) {
      _applyContexts(cached);
    }
    if (mounted) {
      setState(() {
        loadingList = true;
        listError = null;
      });
    }
    try {
      final r = await repository.refreshUsing(
        'context',
        client: widget.env.client,
        path: '/context?limit=30',
        fallback: widget.env.client.listContext,
        versionOf: (_) => DateTime.now().millisecondsSinceEpoch,
      );
      if (mounted) {
        if (r != null) _applyContexts(r);
        setState(() => loadingList = false);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          listError = e;
          loadingList = false;
        });
      }
    }
  }

  void _applyContexts(Map<String, dynamic> r, {bool append = false}) {
    setState(() {
      final page = ((r['contexts'] as List?) ?? const [])
          .map((e) => (e as Map).cast<String, dynamic>())
          .toList();
      contexts = append ? [...contexts, ...page] : page;
      nextCursor = r['next_cursor'] as int?;
      listError = null;
    });
  }

  Future<void> _loadMore() async {
    final cursor = nextCursor;
    if (cursor == null || loadingMore) return;
    setState(() => loadingMore = true);
    try {
      final page = await widget.env.client.listContext(cursor: cursor);
      if (mounted) _applyContexts(page, append: true);
    } catch (e) {
      if (mounted) setState(() => listError = e);
    } finally {
      if (mounted) setState(() => loadingMore = false);
    }
  }

  @override
  void dispose() {
    widget.env.removeListener(_onDataChanged);
    controller.dispose();
    super.dispose();
  }

  void _onDataChanged() => unawaited(_loadList());

  Future<void> _submit() async {
    final text = controller.text.trim();
    if (text.isEmpty || submitting) return;
    setState(() => submitting = true);
    final key = _pendingText == text && _pendingKey != null
        ? _pendingKey!
        : 'context-${DateTime.now().microsecondsSinceEpoch}';
    _pendingText = text;
    _pendingKey = key;
    try {
      final r = await widget.env.client.addContext(text, idempotencyKey: key);
      controller.clear();
      if (!mounted) return;
      _pendingText = null;
      _pendingKey = null;
      setState(() => submitting = false);
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('已保存，正在后台更新健康判断。')));
      final jobId = r['job_id'] as int?;
      if (jobId != null) unawaited(_finishJob(jobId));
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('保存失败：${apiErrorMessage(e)}')));
      }
    } finally {
      if (mounted) setState(() => submitting = false);
    }
  }

  Future<void> _finishJob(int jobId) async {
    var delay = const Duration(milliseconds: 500);
    for (var attempt = 0; attempt < 6; attempt++) {
      await Future<void>.delayed(delay);
      try {
        final status = await widget.env.client.getJobStatus(jobId);
        if (status['status'] == 'SUCCEEDED') {
          final repository = await widget.env.repository();
          await repository.cache.invalidate('context');
          await repository.cache.invalidate('history');
          await repository.cache.invalidate('today');
          widget.env.notifyDataChanged();
          if (mounted) {
            ScaffoldMessenger.of(
              context,
            ).showSnackBar(const SnackBar(content: Text('后台更新已完成，今日判断已刷新。')));
          }
          return;
        }
        if (status['status'] == 'FAILED') {
          throw const ApiException(ApiErrorKind.server, '后台处理失败，请重新提交');
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

  Future<void> _correct(Map<String, dynamic> ctx) async {
    final editController = TextEditingController(
      text: ctx['raw_text'] as String? ?? '',
    );
    final text = await showDialog<String>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('纠正这条情况'),
        content: TextField(
          controller: editController,
          maxLines: 3,
          autofocus: true,
          decoration: const InputDecoration(hintText: '真实情况是什么？'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, editController.text),
            child: const Text('纠正'),
          ),
        ],
      ),
    );
    if (text == null || text.trim().isEmpty) return;
    try {
      final r = await widget.env.client.correctContext(
        ctx['id'] as int,
        text.trim(),
        idempotencyKey:
            'context-correct-${ctx['id']}-${DateTime.now().microsecondsSinceEpoch}',
      );
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('纠正已保存，正在后台更新。')));
      }
      final jobId = r['job_id'] as int?;
      if (jobId != null) unawaited(_finishJob(jobId));
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('纠正失败：${apiErrorMessage(e)}')));
      }
    }
  }

  Future<void> _delete(Map<String, dynamic> ctx) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('删除这条情况？'),
        content: const Text('删除不会真正擦除历史，而是标记为用户删除（可追溯）。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      final r = await widget.env.client.deleteContext(
        ctx['id'] as int,
        idempotencyKey:
            'context-delete-${ctx['id']}-${DateTime.now().microsecondsSinceEpoch}',
      );
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('删除已保存，正在后台更新。')));
      }
      final jobId = r['job_id'] as int?;
      if (jobId != null) unawaited(_finishJob(jobId));
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('删除失败：${apiErrorMessage(e)}')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('补充我的情况')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(
            '用一句话说明你的现实情况即可。保存是自动的，不会弹确认框；你可以随时纠正或删除。',
            style: TextStyle(fontSize: 13, color: Colors.black54, height: 1.5),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: controller,
            maxLines: 3,
            decoration: const InputDecoration(
              hintText: '例如：昨晚两点才睡 / 今天练了腿，强度很大 / 有点头疼',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final c in quickChips)
                ActionChip(
                  label: Text(c),
                  onPressed: submitting
                      ? null
                      : () => setState(
                          () => controller.text = controller.text.isEmpty
                              ? c
                              : '${controller.text}，$c',
                        ),
                ),
            ],
          ),
          const SizedBox(height: 16),
          FilledButton(
            onPressed: submitting ? null : _submit,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: submitting
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('保存'),
            ),
          ),
          const SizedBox(height: 24),
          Row(
            children: [
              const Text(
                '当前有效的情况',
                style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15),
              ),
              const Spacer(),
              IconButton(
                onPressed: _loadList,
                icon: const Icon(Icons.refresh, size: 18),
                tooltip: '刷新',
              ),
            ],
          ),
          if (loadingList) const LinearProgressIndicator(minHeight: 2),
          if (listError != null)
            ApiErrorView(error: listError!, onRetry: _loadList),
          if (!loadingList && contexts.isEmpty && listError == null)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 12),
              child: Text('暂无。', style: TextStyle(color: Colors.black45)),
            ),
          for (final ctx in contexts)
            Card(
              margin: const EdgeInsets.symmetric(vertical: 4),
              child: ListTile(
                leading: CircleAvatar(
                  radius: 16,
                  backgroundColor: Colors.black.withOpacity(0.06),
                  child: const Icon(
                    Icons.event_note,
                    size: 16,
                    color: Colors.black54,
                  ),
                ),
                title: Text(
                  '${ctx['context_type_label'] ?? '其他个人情况'}'
                  '${ctx['body_part_label'] != null ? ' · ${ctx['body_part_label']}' : ''}',
                ),
                subtitle: Text(
                  '${ctx['context_date']} · ${ctx['raw_text'] ?? ''}',
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                trailing: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    IconButton(
                      icon: const Icon(Icons.edit_outlined, size: 18),
                      tooltip: '纠正',
                      onPressed: () => _correct(ctx),
                    ),
                    IconButton(
                      icon: const Icon(Icons.delete_outline, size: 18),
                      tooltip: '删除',
                      onPressed: () => _delete(ctx),
                    ),
                  ],
                ),
              ),
            ),
          if (nextCursor != null)
            Center(
              child: TextButton.icon(
                onPressed: loadingMore ? null : _loadMore,
                icon: const Icon(Icons.expand_more),
                label: const Text('加载更早情况'),
              ),
            ),
          const SizedBox(height: 16),
          const Card(
            child: Padding(
              padding: EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '关于个人情况的几个原则',
                    style: TextStyle(fontWeight: FontWeight.w600),
                  ),
                  SizedBox(height: 8),
                  Text(
                    '· 允许模糊信息（喝酒=有，量=未知）\n'
                    '· 一次性事件不会永久保留\n'
                    '· 持续症状会到期复核，不会一直有效\n'
                    '· 你的显式纠错 > AI 结构化 > AI 推断',
                    style: TextStyle(
                      fontSize: 12,
                      color: Colors.black54,
                      height: 1.6,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
