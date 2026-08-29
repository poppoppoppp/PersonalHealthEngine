/// 我的 — settings, connection, engine usage. No scores, no grades (§13).
library;

import 'package:flutter/material.dart';

import '../main.dart';
import '../widgets/api_error_view.dart';
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
  Object? error;

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
          error = null;
          quietController.text = '${settings!['quiet_hours'] ?? ''}';
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = e);
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
                  ? error == null
                        ? const Text('加载中…')
                        : ApiErrorView(error: error!, onRetry: _load)
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
                          leading: const Icon(
                            Icons.history_toggle_off,
                            color: Colors.black54,
                          ),
                          title: const Text('通知与决策记录'),
                          subtitle: const Text('已发送与被抑制的通知都留痕'),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: () => Navigator.of(context).push(
                            MaterialPageRoute(
                              builder: (_) =>
                                  NotificationsScreen(env: widget.env),
                            ),
                          ),
                        ),
                      ],
                    ),
            ),
          ),
          const SizedBox(height: 16),
          _sectionTitle('关于这个应用'),
          const Card(
            child: Padding(
              padding: EdgeInsets.all(14),
              child: Text(
                'PHE 只对比"现在的你"和"你自己的长期正常状态"，'
                '帮你决定今天怎么安排。它不做诊断，没有健康分，'
                '你补充的每一条情况都会让它更懂你。',
                style: TextStyle(
                  fontSize: 13,
                  height: 1.7,
                  color: Colors.black87,
                ),
              ),
            ),
          ),
          const SizedBox(height: 16),
          _sectionTitle('技术信息'),
          Card(
            child: ExpansionTile(
              title: const Text('连接与工作量'),
              subtitle: const Text(
                '服务器连接、引擎工作量统计。日常使用不需要打开。',
              ),
              childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
              children: [
                TextField(
                  controller: urlController,
                  decoration: const InputDecoration(
                    labelText: '服务器地址',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 8),
                TextField(
                  controller: tokenController,
                  obscureText: true,
                  enableSuggestions: false,
                  autocorrect: false,
                  decoration: const InputDecoration(
                    labelText: '访问令牌',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 8),
                Align(
                  alignment: Alignment.centerRight,
                  child: FilledButton.tonal(
                    onPressed: () async {
                      await widget.env.updateConnection(
                        urlController.text,
                        tokenController.text,
                      );
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
                const Divider(height: 24),
                Align(
                  alignment: Alignment.centerLeft,
                  child: usage == null
                      ? const Text(
                          '暂无工作量数据',
                          style: TextStyle(color: Colors.black45, fontSize: 12.5),
                        )
                      : Text(
                          '引擎累计完成 ${usage!['eval_runs']} 次完整分析；'
                          '其中 ${usage!['total_model_calls']} 次用到了人工智能，'
                          '其余都靠直接复用之前的结果完成（相同的数据不重复计算）。',
                          style: const TextStyle(
                            fontSize: 12.5,
                            height: 1.7,
                            color: Colors.black87,
                          ),
                        ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _sectionTitle(String t) => Padding(
    padding: const EdgeInsets.only(bottom: 8, top: 4),
    child: Text(
      t,
      style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15),
    ),
  );

  Future<void> _setMode(String mode) async {
    try {
      await widget.env.client.putSettings({'notification_mode': mode});
      await _load();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(
          SnackBar(content: Text('保存失败：${apiErrorMessage(e)}')),
        );
      }
    }
  }

  Future<void> _saveQuiet() async {
    final v = quietController.text.trim();
    try {
      await widget.env.client.putSettings({
        'quiet_hours': v.isEmpty ? null : v,
      });
      await _load();
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('免打扰时段已保存')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(
          SnackBar(content: Text('保存失败：${apiErrorMessage(e)}')),
        );
      }
    }
  }
}
