/// 我的 — 数据概览与引擎状态（晨间简报版权页）。设置项收纳在页面底部。
library;

import 'package:flutter/material.dart';

import '../design.dart';
import '../main.dart';
import '../widgets/api_error_view.dart';
import '../widgets/masthead.dart';
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
  Map<String, dynamic>? today;
  List<dynamic>? runs;
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
      Map<String, dynamic>? t;
      List<dynamic>? r;
      try {
        u = await widget.env.client.getUsage();
      } catch (_) {}
      try {
        t = (await widget.env.client.getToday()).raw;
      } catch (_) {}
      try {
        r = (await widget.env.client.getEvalRuns())['runs'] as List?;
      } catch (_) {}
      if (mounted) {
        setState(() {
          settings = (s['settings'] as Map).cast<String, dynamic>();
          usage = u;
          today = t;
          runs = r;
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

  bool _reanalyzing = false;
  String? _reanalyzeNote;

  Future<void> _reanalyze() async {
    setState(() {
      _reanalyzing = true;
      _reanalyzeNote = null;
    });
    try {
      await widget.env.client.refreshToday();
      if (mounted) _reanalyzeNote = '分析完成';
      await _load();
    } catch (e) {
      if (mounted) _reanalyzeNote = '暂时没有完成，稍后再试';
    } finally {
      if (mounted) setState(() => _reanalyzing = false);
    }
  }

  String _fmtDate(String? iso) {
    if (iso == null || iso.isEmpty) return '暂无';
    final parts = iso.split('-');
    if (parts.length != 3) return iso;
    return '${int.parse(parts[1])}月${int.parse(parts[2])}日';
  }

  String _todayMark() {
    final date = today?['analysis_date'] as String?;
    if (date == null) return '暂无';
    final now = DateTime.now();
    final todayDate =
        '${now.year.toString().padLeft(4, '0')}-${now.month.toString().padLeft(2, '0')}-${now.day.toString().padLeft(2, '0')}';
    if (date == todayDate) return '已完成';
    return _fmtDate(date);
  }

  String? _lastRunLabel() {
    if (runs == null || runs!.isEmpty) return null;
    final first = runs!.first as Map;
    final started = '${first['started_at_utc'] ?? ''}';
    if (started.isEmpty) return null;
    try {
      final dt = DateTime.parse(started).toLocal();
      final now = DateTime.now();
      final sameDay = dt.year == now.year && dt.month == now.month && dt.day == now.day;
      final hh = dt.hour.toString().padLeft(2, '0');
      final mm = dt.minute.toString().padLeft(2, '0');
      return sameDay ? '今天 $hh:$mm' : '${dt.month}/${dt.day} $hh:$mm';
    } catch (_) {
      return null;
    }
  }

  String _nextUpdateLabel() {
    final now = DateTime.now();
    var next = DateTime(now.year, now.month, now.day, now.hour >= 22 ? 0 : now.hour + 2, 30);
    if (next.isBefore(now)) {
      next = next.add(const Duration(hours: 2));
      if (next.day != now.day) {
        next = DateTime(now.year, now.month, now.day + 1, 0, 30);
      }
    }
    final hh = next.hour.toString().padLeft(2, '0');
    final mm = next.minute.toString().padLeft(2, '0');
    return sameDayLabel(next, hh, mm);
  }

  String sameDayLabel(DateTime next, String hh, String mm) {
    final now = DateTime.now();
    final label = '$hh:$mm';
    return next.day == now.day ? '今天 $label' : '明天 $label';
  }

  @override
  Widget build(BuildContext context) {
    final lastRun = _lastRunLabel();
    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(0, 0, 0, 24),
          children: [
            const Masthead(brand: 'PHE 版权页', title: '我的 · 数据与引擎状态'),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (error != null) ...[
                    ApiErrorView(error: error!, onRetry: _load),
                    const SizedBox(height: 12),
                  ],
                  const SectionLabel('数据概览'),
                  Row(
                    children: [
                      Expanded(
                        child: _statCard('最新数据', _fmtDate(today?['analysis_date'] as String?)),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: _statCard('今日分析', _todayMark(), done: _todayMark() == '已完成'),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  const SectionLabel('引擎在做什么'),
                  _engineCard(lastRun),
                  const Padding(
                    padding: EdgeInsets.fromLTRB(2, 10, 2, 0),
                    child: Text(
                      '手环数据自动同步，你补充的情况和纠正会让分析越来越准。不需要做任何操作。',
                      style: TextStyle(fontSize: 11.5, color: Ed.inkFaint, height: 1.8),
                    ),
                  ),
                  const SectionLabel('通知'),
                  _settingsCard(),
                  const SectionLabel('记录'),
                  Card(
                    margin: EdgeInsets.zero,
                    child: ListTile(
                      title: const Text(
                        '通知与决策记录',
                        style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700),
                      ),
                      subtitle: const Text(
                        '已发送与被抑制的通知都留痕',
                        style: TextStyle(fontSize: 11.5, color: Ed.inkSoft),
                      ),
                      trailing: const Icon(Icons.chevron_right, color: Ed.inkSoft),
                      onTap: () => Navigator.of(context).push(
                        MaterialPageRoute(
                          builder: (_) => NotificationsScreen(env: widget.env),
                        ),
                      ),
                    ),
                  ),
                  const SectionLabel('关于'),
                  const Text(
                    'PHE 只对比「现在的你」和「你自己的长期正常状态」，帮你决定今天怎么安排。'
                    '它不做诊断，没有健康分，你补充的每一条情况都会让它更懂你。',
                    style: TextStyle(fontSize: 13, height: 1.85, color: Ed.ink),
                  ),
                  const SectionLabel('技术信息'),
                  _techCard(),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _statCard(String k, String v, {bool done = false}) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 10),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: Ed.hairline),
      ),
      child: Column(
        children: [
          Text(k, style: const TextStyle(fontSize: 10.5, color: Ed.inkSoft)),
          const SizedBox(height: 4),
          Text(
            v,
            style: TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w900,
              color: done ? Ed.good : Ed.ink,
            ),
          ),
        ],
      ),
    );
  }

  Widget _engineCard(String? lastRun) {
    return Container(
      padding: const EdgeInsets.all(15),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: Ed.hairline),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _engineLine(
            done: true,
            text: lastRun == null
                ? '引擎待命 —— 打开「今日」下拉刷新即可手动分析'
                : '$lastRun —— 已完成今日分析，结论在「今日」',
          ),
          const SizedBox(height: 8),
          _engineLine(done: true, text: '数据每 2 小时自动同步补充一次'),
          const SizedBox(height: 8),
          _engineLine(done: false, text: '下次自动更新 —— ${_nextUpdateLabel()}'),
          const SizedBox(height: 12),
          SizedBox(
            width: double.infinity,
            child: FilledButton.tonal(
              onPressed: _reanalyzing ? null : _reanalyze,
              style: const ButtonStyle(
                backgroundColor: WidgetStatePropertyAll(Color(0xFFF0E9E0)),
                foregroundColor: WidgetStatePropertyAll(Ed.ink),
              ),
              child: _reanalyzing
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('立即重新分析', style: TextStyle(fontSize: 13)),
            ),
          ),
          if (_reanalyzeNote != null)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(
                _reanalyzeNote!,
                style: const TextStyle(fontSize: 11.5, color: Ed.inkSoft),
              ),
            ),
        ],
      ),
    );
  }

  Widget _engineLine({required bool done, required String text}) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          margin: const EdgeInsets.only(top: 6),
          width: 8,
          height: 8,
          decoration: BoxDecoration(
            color: done ? Ed.data : Ed.seal,
            shape: BoxShape.circle,
          ),
        ),
        const SizedBox(width: 9),
        Expanded(
          child: Text(
            text,
            style: const TextStyle(fontSize: 12, color: Ed.inkSoft, height: 1.6),
          ),
        ),
      ],
    );
  }

  Widget _settingsCard() {
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        child: Column(
          children: [
            RadioListTile<String>(
              title: const Text('智能通知（推荐）', style: TextStyle(fontSize: 14)),
              subtitle: const Text(
                '只在重要变化时提醒',
                style: TextStyle(fontSize: 11.5, color: Ed.inkSoft),
              ),
              value: 'SMART',
              groupValue: settings?['notification_mode'] as String?,
              onChanged: (v) => v == null ? null : _setMode(v),
            ),
            RadioListTile<String>(
              title: const Text('安静', style: TextStyle(fontSize: 14)),
              subtitle: const Text(
                '仅重要安全事件',
                style: TextStyle(fontSize: 11.5, color: Ed.inkSoft),
              ),
              value: 'QUIET',
              groupValue: settings?['notification_mode'] as String?,
              onChanged: (v) => v == null ? null : _setMode(v),
            ),
            RadioListTile<String>(
              title: const Text('每日', style: TextStyle(fontSize: 14)),
              subtitle: const Text(
                '每天状态更新 + 重要变化',
                style: TextStyle(fontSize: 11.5, color: Ed.inkSoft),
              ),
              value: 'DAILY',
              groupValue: settings?['notification_mode'] as String?,
              onChanged: (v) => v == null ? null : _setMode(v),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: quietController,
                      decoration: const InputDecoration(
                        labelText: '免打扰时段（如 22:00-07:00）',
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
          ],
        ),
      ),
    );
  }

  Widget _techCard() {
    return Card(
      margin: EdgeInsets.zero,
      child: ExpansionTile(
        title: const Text(
          '连接与版本',
          style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700),
        ),
        subtitle: const Text(
          '服务器连接、令牌与版本。日常使用不需要打开。',
          style: TextStyle(fontSize: 11.5, color: Ed.inkSoft),
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
                    today = null;
                    runs = null;
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
            child: Text(
              usage == null
                  ? '暂无工作量数据'
                  : '引擎累计完成 ${usage!['eval_runs']} 次完整分析，'
                      '其中 ${usage!['total_model_calls']} 次用到人工智能，'
                      '其余靠复用之前的结果完成。',
              style: const TextStyle(fontSize: 12.5, height: 1.7, color: Ed.inkSoft),
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
