/// 我的规律 — Personal Patterns (§33–§36, §42, §46). Only patterns with real action value
/// are shown; everything else stays in the accumulation state. Single events never become
/// patterns, and counterevidence is always visible.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../main.dart';
import '../widgets/masthead.dart';
import '../widgets/api_error_view.dart';

class PatternsScreen extends StatefulWidget {
  final AppEnv env;
  const PatternsScreen({super.key, required this.env});
  @override
  State<PatternsScreen> createState() => _PatternsScreenState();
}

class _PatternsScreenState extends State<PatternsScreen> {
  Map<String, dynamic>? data;
  Object? error;
  bool loading = false;

  @override
  void initState() {
    super.initState();
    widget.env.addListener(_onDataChanged);
    _load();
  }

  void _onDataChanged() => unawaited(_load());

  @override
  void dispose() {
    widget.env.removeListener(_onDataChanged);
    super.dispose();
  }

  Future<void> _load() async {
    final repository = await widget.env.repository();
    final cached = await repository.cached('patterns');
    if (cached != null && mounted && data == null) {
      setState(() => data = cached);
    }
    if (mounted) setState(() => loading = true);
    try {
      final r = await repository.refreshUsing(
        'patterns',
        client: widget.env.client,
        path: '/patterns',
        fallback: widget.env.client.getPatterns,
        versionOf: (_) => DateTime.now().millisecondsSinceEpoch,
      );
      if (mounted) {
        setState(() {
          data = r ?? data;
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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: RefreshIndicator(
          onRefresh: _load,
          child: ListView(
          padding: const EdgeInsets.fromLTRB(0, 0, 0, 16),
          children: [
            const Masthead(brand: 'PHE 观察', title: '我的规律 · 反复验证的关联'),
            const SizedBox(height: 14),
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 20),
              child: Text(
              '规律 = 系统从重复历史中学到的、有现实行动价值的个人模式。'
              '它与“历史”不同：历史是发生过什么，规律是学到了什么。',
              style: TextStyle(
                fontSize: 12.5,
                color: Colors.black54,
                height: 1.6,
              ),
              ),
            ),
            const SizedBox(height: 12),
            if (loading && data != null)
              const LinearProgressIndicator(minHeight: 2),
            if (error != null) ApiErrorView(error: error!, onRetry: _load),
            if (data == null && error == null)
              const Padding(
                padding: EdgeInsets.only(top: 40),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (data != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20),
              child: Column(children: [..._body()]),
            ),
          ],
          ),
        ),
      ),
    );
  }

  List<Widget> _body() {
    final patterns = (data!['patterns'] as List)
        .map((e) => (e as Map).cast<String, dynamic>())
        .toList();
    final observing = data!['observing_count'] as int? ?? 0;
    final note = data!['accumulation_note'] as String?;

    final widgets = <Widget>[];
    if (patterns.isEmpty) {
      widgets.add(
        const Card(
          child: Padding(
            padding: EdgeInsets.all(20),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '还没有足够证据形成规律',
                  style: TextStyle(fontWeight: FontWeight.w600),
                ),
                SizedBox(height: 8),
                Text(
                  '单次事件不会直接形成规律。需要多次独立事件、足够时间跨度、一致性与合理数据质量。',
                  style: TextStyle(
                    fontSize: 12.5,
                    color: Colors.black54,
                    height: 1.6,
                  ),
                ),
              ],
            ),
          ),
        ),
      );
    } else {
      for (final p in patterns) {
        final displayLabel = p['display_status_label'] ?? '正在观察';
        widgets.add(
          Card(
            margin: const EdgeInsets.only(bottom: 12),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '${p['description']}',
                    style: const TextStyle(fontSize: 14, height: 1.5),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '状态：$displayLabel · 时间跨度 ${p['first_seen_date']} ~ ${p['last_seen_date']}',
                    style: const TextStyle(fontSize: 11, color: Colors.black45),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    '反例 ${p['counter_examples']} 次（规律不只看支持证据）',
                    style: const TextStyle(fontSize: 11, color: Colors.black45),
                  ),
                ],
              ),
            ),
          ),
        );
      }
    }

    if (observing > 0) {
      widgets.add(
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: Colors.black.withOpacity(0.04),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Row(
            children: [
              const Icon(Icons.hourglass_top, size: 18, color: Colors.black45),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  '正在积累证据的观察项：$observing 条。${note ?? ''}',
                  style: const TextStyle(
                    fontSize: 12.5,
                    color: Colors.black54,
                    height: 1.5,
                  ),
                ),
              ),
            ],
          ),
        ),
      );
    }
    return widgets;
  }
}
