/// 通知记录 — what was sent, what was suppressed, and why (§48–§52).
/// The decision audit log makes "为什么没通知 / 为什么通知了" always answerable.
library;

import 'package:flutter/material.dart';

import '../main.dart';

class NotificationsScreen extends StatefulWidget {
  final AppEnv env;
  const NotificationsScreen({super.key, required this.env});
  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  List<dynamic> sent = [];
  List<dynamic> decisions = [];
  String? error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final f = await widget.env.client.getNotifications();
      final d = await widget.env.client.getNotificationDecisions();
      if (mounted) {
        setState(() {
          sent = (f['notifications'] as List? ?? const []);
          decisions = (d['decisions'] as List? ?? const []);
          error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  String _reasonZh(String r) => switch (r) {
        'safety_attention' => '健康安全关注',
        'actionable_change' => '出现需要行动的变化',
        'daily_update' => '每日状态更新',
        'quiet_mode' => '安静模式（仅安全事件）',
        'quiet_hours' => '免打扰时段',
        'smart_mode_no_action_needed' => '智能模式：无需行动',
        'no_new_judgment' => '判断未更新',
        _ => r,
      };

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('通知与决策记录')),
      body: error != null
          ? Center(child: Text(error!))
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  const Text('已发送',
                      style:
                          TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
                  const SizedBox(height: 8),
                  if (sent.isEmpty)
                    const Text('还没有发送过通知。稳定日不打扰是默认行为。',
                        style: TextStyle(color: Colors.black45, fontSize: 13)),
                  for (final n in sent.map((e) => (e as Map).cast<String, dynamic>()))
                    Card(
                      margin: const EdgeInsets.only(bottom: 8),
                      child: ListTile(
                        leading: const Icon(Icons.notifications_active_outlined,
                            color: Colors.black54),
                        title: Text('${n['headline'] ?? ''}',
                            style: const TextStyle(fontSize: 14)),
                        subtitle: Text(
                            '${n['created_at_utc']} · ${_reasonZh('${n['reason']}')}',
                            style: const TextStyle(fontSize: 11)),
                      ),
                    ),
                  const SizedBox(height: 16),
                  const Text('决策审计（含被抑制的）',
                      style:
                          TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
                  const SizedBox(height: 4),
                  const Text('每一次“通知/不通知”都留痕。',
                      style: TextStyle(color: Colors.black45, fontSize: 12)),
                  const SizedBox(height: 8),
                  for (final d in decisions
                      .map((e) => (e as Map).cast<String, dynamic>()))
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 3),
                      child: Row(
                        children: [
                          Icon(
                            d['decision'] == 'SEND'
                                ? Icons.check_circle
                                : Icons.block,
                            size: 16,
                            color: d['decision'] == 'SEND'
                                ? const Color(0xFF4A6B57)
                                : Colors.black38,
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              '${d['decision']} · ${_reasonZh('${d['reason']}')} · ${d['mode']}',
                              style: const TextStyle(
                                  fontSize: 12, color: Colors.black87),
                            ),
                          ),
                        ],
                      ),
                    ),
                ],
              ),
            ),
    );
  }
}
