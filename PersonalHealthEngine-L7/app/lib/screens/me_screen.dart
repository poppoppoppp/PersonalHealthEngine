/// 我的 — settings, connection, engine usage. No scores, no grades (§13).
library;

import 'package:flutter/material.dart';

import '../main.dart';
import 'notifications_screen.dart';

class MeScreen extends StatefulWidget {
  final AppEnv env;
  const MeScreen({super.key, required this.env});
  @override
  State<MeScreen> createState() => _MeScreenState();
}

class _MeScreenState extends State<MeScreen> {
  Map<String, dynamic>? settings;
  Map<String, dynamic>? usage;
  String? error;

  final urlController = TextEditingController();
  final tokenController = TextEditingController();
  final quietController = TextEditingController();

  @override
  void initState() {
    super.initState();
    urlController.text = widget.env.baseUrl;
    tokenController.text = widget.env.token;
    _load();
  }

  Future<void> _load() async {
    try {
      final s = await widget.env.client.getSettings();
      Map<String, dynamic>? u;
      try {
        u = await widget.env.client.getUsage();
      } catch (_) {}
      if (mounted) {
        setState(() {
          settings = (s['settings'] as Map).cast<String, dynamic>();
          usage = u;
          quietController.text = '${settings!['quiet_hours'] ?? ''}';
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  @override
  void dispose() {
    urlController.dispose();
    tokenController.dispose();
    quietController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('我的')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _sectionTitle('通知'),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: settings == null
                  ? Text(error ?? '加载中…')
                  : Column(
                      children: [
                        RadioListTile<String>(
                          title: const Text('安静'),
                          subtitle: const Text('仅重要安全事件'),
                          value: 'QUIET',
                          groupValue: settings!['notification_mode'],
                          onChanged: (v) => _setMode(v!),
                        ),
                        RadioListTile<String>(
                          title: const Text('智能（默认）'),
                          subtitle: const Text('只通知可能影响下一步决策的重大变化；稳定日不打扰'),
                          value: 'SMART',
                          groupValue: settings!['notification_mode'],
                          onChanged: (v) => _setMode(v!),
                        ),
                        RadioListTile<String>(
                          title: const Text('每日'),
                          subtitle: const Text('每天状态更新 + 重要变化'),
                          value: 'DAILY',
                          groupValue: settings!['notification_mode'],
                          onChanged: (v) => _setMode(v!),
                        ),
                        const Divider(height: 18),
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 16),
                          child: Row(
                            children: [
                              Expanded(
                                child: TextField(
                                  controller: quietController,
                                  decoration: const InputDecoration(
                                    labelText: '免打扰时段（如 22:00-07:00，留空关闭）',
                                    isDense: true,
                                    border: OutlineInputBorder(),
                                  ),
                                ),
                              ),
                              const SizedBox(width: 8),
                              FilledButton.tonal(
                                onPressed: _saveQuiet,
                                child: const Text('保存'),
                              ),
                            ],
                          ),
                        ),
                        ListTile(
                          leading: const Icon(Icons.history_toggle_off,
                              color: Colors.black54),
                          title: const Text('通知与决策记录'),
                          subtitle: const Text('已发送与被抑制的通知都留痕'),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: () => Navigator.of(context).push(
                            MaterialPageRoute(
                                builder: (_) =>
                                    NotificationsScreen(env: widget.env)),
                          ),
                        ),
                      ],
                    ),
            ),
          ),
          const SizedBox(height: 16),
          _sectionTitle('引擎连接'),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                children: [
                  TextField(
                    controller: urlController,
                    decoration: const InputDecoration(
                        labelText: '服务器地址', border: OutlineInputBorder()),
                  ),
                  const SizedBox(height: 8),
                  TextField(
                    controller: tokenController,
                    decoration: const InputDecoration(
                        labelText: '访问令牌', border: OutlineInputBorder()),
                  ),
                  const SizedBox(height: 8),
                  Align(
                    alignment: Alignment.centerRight,
                    child: FilledButton.tonal(
                      onPressed: () async {
                        await widget.env.updateConnection(
                            urlController.text, tokenController.text);
                        if (mounted) {
                          setState(() {
                            settings = null;
                            usage = null;
                            error = null;
                          });
                          _load();
                        }
                      },
                      child: const Text('保存并测试'),
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          _sectionTitle('模型调用（成本透明）'),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: usage == null
                  ? const Text('暂无数据',
                      style: TextStyle(color: Colors.black45))
                  : Text(
                      '评估运行 ${usage!['eval_runs']} 次 · 实际模型调用 ${usage!['total_model_calls']} 次 · 缓存命中项 ${usage!['cached_entries']} 条\n'
                      '原则：科学性不能省，不必要的模型调用必须省。',
                      style: const TextStyle(
                          fontSize: 12.5, height: 1.7, color: Colors.black87),
                    ),
            ),
          ),
          const SizedBox(height: 16),
          _sectionTitle('关于'),
          const Card(
            child: Padding(
              padding: EdgeInsets.all(14),
              child: Text(
                'Personal Health Engine · Layer 7\n'
                '核心逻辑：现在的你 vs 你自己的长期正常状态。\n'
                '本应用不含任何健康分、恢复分或红黄绿灯。',
                style: TextStyle(fontSize: 12.5, height: 1.7, color: Colors.black54),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _sectionTitle(String t) => Padding(
        padding: const EdgeInsets.only(bottom: 8, top: 4),
        child: Text(t,
            style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
      );

  Future<void> _setMode(String mode) async {
    try {
      await widget.env.client.putSettings({'notification_mode': mode});
      await _load();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('保存失败：$e')));
      }
    }
  }

  Future<void> _saveQuiet() async {
    final v = quietController.text.trim();
    try {
      await widget.env.client
          .putSettings({'quiet_hours': v.isEmpty ? null : v});
      await _load();
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('免打扰时段已保存')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('保存失败：$e')));
      }
    }
  }
}
