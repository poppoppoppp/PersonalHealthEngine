/// Context capture (§23–§28). Natural language first, quick chips second, never a daily
/// questionnaire. Auto-save without confirmation dialogs; corrections supersede old facts
/// with full provenance.
library;

import 'package:flutter/material.dart';

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

  static const quickChips = ['熬夜', '高强度训练', '喝酒', '身体不舒服', '压力大'];

  @override
  void initState() {
    super.initState();
    _loadList();
  }

  Future<void> _loadList() async {
    setState(() {
      loadingList = true;
      listError = null;
    });
    try {
      final r = await widget.env.client.listContext();
      if (mounted) {
        setState(() {
          contexts = ((r['contexts'] as List?) ?? const [])
              .map((e) => (e as Map).cast<String, dynamic>())
              .toList();
          loadingList = false;
          listError = null;
        });
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

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final text = controller.text.trim();
    if (text.isEmpty || submitting) return;
    setState(() => submitting = true);
    try {
      final r = await widget.env.client.addContext(text);
      controller.clear();
      await _loadList();
      if (!mounted) return;
      if (r['status'] == 'SAVED') {
        final re = (r['re_evaluation'] as Map?) ?? const {};
        final updated = re['judgment_updated'] == true;
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(updated
              ? '已记录。今日判断已根据新情况更新。'
              : '已记录。引擎已复核，今日判断保持不变。'),
        ));
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${r['note'] ?? '没有识别出结构化事实'}')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('保存失败：${apiErrorMessage(e)}')),
        );
      }
    } finally {
      if (mounted) setState(() => submitting = false);
    }
  }

  Future<void> _correct(Map<String, dynamic> ctx) async {
    final editController =
        TextEditingController(text: ctx['raw_text'] as String? ?? '');
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
              onPressed: () => Navigator.pop(context), child: const Text('取消')),
          FilledButton(
              onPressed: () => Navigator.pop(context, editController.text),
              child: const Text('纠正')),
        ],
      ),
    );
    if (text == null || text.trim().isEmpty) return;
    try {
      final r = await widget.env.client
          .correctContext(ctx['id'] as int, text.trim());
      await _loadList();
      if (mounted) {
        final updated =
            ((r['re_evaluation'] as Map?)?['judgment_updated']) == true;
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(updated ? '已纠正，今日判断已更新。' : '已纠正，今日判断保持不变。'),
        ));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('纠正失败：${apiErrorMessage(e)}')),
        );
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
              child: const Text('取消')),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('删除')),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await widget.env.client.deleteContext(ctx['id'] as int);
      await _loadList();
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('已删除，今日判断已复核。')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('删除失败：${apiErrorMessage(e)}')),
        );
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
                      : () => setState(() => controller.text =
                          controller.text.isEmpty
                              ? c
                              : '${controller.text}，$c'),
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
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : const Text('保存'),
            ),
          ),
          const SizedBox(height: 24),
          Row(
            children: [
              const Text('当前有效的情况',
                  style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
              const Spacer(),
              IconButton(
                onPressed: _loadList,
                icon: const Icon(Icons.refresh, size: 18),
                tooltip: '刷新',
              ),
            ],
          ),
          if (loadingList)
            const Padding(
              padding: EdgeInsets.only(top: 16),
              child: Center(child: CircularProgressIndicator()),
            ),
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
                  child: const Icon(Icons.event_note,
                      size: 16, color: Colors.black54),
                ),
                title: Text('${ctx['context_type_label'] ?? '其他个人情况'}'
                    '${ctx['body_part_label'] != null ? ' · ${ctx['body_part_label']}' : ''}'),
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
          const SizedBox(height: 16),
          const Card(
            child: Padding(
              padding: EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('关于个人情况的几个原则',
                      style: TextStyle(fontWeight: FontWeight.w600)),
                  SizedBox(height: 8),
                  Text(
                    '· 允许模糊信息（喝酒=有，量=未知）\n'
                    '· 一次性事件不会永久保留\n'
                    '· 持续症状会到期复核，不会一直有效\n'
                    '· 你的显式纠错 > AI 结构化 > AI 推断',
                    style: TextStyle(
                        fontSize: 12, color: Colors.black54, height: 1.6),
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
