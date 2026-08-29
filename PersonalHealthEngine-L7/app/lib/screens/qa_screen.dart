/// Q&A — Personal Health Decision Assistant (§18–§22).
/// Answer-first. Health facts always come from the engine; the conversation only carries
/// dialogue. Medical review is applied by the sealed L6 policy when triggered.
library;

import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';

import '../api_client.dart';
import '../design.dart';
import '../main.dart';
import '../widgets/masthead.dart';
import '../widgets/api_error_view.dart';

class QnAScreen extends StatefulWidget {
  final AppEnv env;
  const QnAScreen({super.key, required this.env});
  @override
  State<QnAScreen> createState() => _QnAScreenState();
}

class _QnAScreenState extends State<QnAScreen> {
  static const _pendingJobKey = 'qa.pending-job.v1';
  final controller = TextEditingController();
  final scrollController = ScrollController();
  final List<_Msg> messages = [];
  int? conversationId;
  bool busy = false;
  int loadingStage = 0;
  Timer? loadingTimer;
  String? pendingQuestion;
  String? pendingKey;
  bool pendingPersisted = false;
  int submissionSequence = 0;

  static const loadingMessages = ['正在理解你的问题', '正在结合你的健康数据', '正在进行安全检查（如需要）'];
  static const acceptedLoadingMessage = '已收到，正在进行安全检查（可先离开）';

  static const suggestions = [
    '今天能不能练腿？',
    '今天适合跑步吗？',
    '昨晚睡差了，今天咖啡什么时候喝？',
    '今天适合安排高强度学习吗？',
  ];

  @override
  void initState() {
    super.initState();
    unawaited(_resumePending());
  }

  @override
  void dispose() {
    loadingTimer?.cancel();
    controller.dispose();
    scrollController.dispose();
    super.dispose();
  }

  void _startLoadingStages({int initialStage = 0}) {
    loadingTimer?.cancel();
    loadingStage = initialStage;
    loadingTimer = Timer.periodic(const Duration(seconds: 3), (timer) {
      if (!mounted || !busy) {
        timer.cancel();
        return;
      }
      if (loadingStage < loadingMessages.length - 1) {
        setState(() => loadingStage += 1);
      } else {
        timer.cancel();
      }
    });
  }

  void _stopLoadingStages() {
    loadingTimer?.cancel();
    loadingTimer = null;
  }

  void _scrollDown() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (scrollController.hasClients) {
        scrollController.animateTo(
          scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  String _newSubmissionKey() =>
      'qa-${DateTime.now().microsecondsSinceEpoch.toRadixString(36)}-${submissionSequence++}';

  String get _loadingMessage =>
      pendingPersisted ? acceptedLoadingMessage : loadingMessages[loadingStage];

  Future<void> _rememberPending(int jobId) async {
    final prefs = await widget.env.sharedPreferences();
    await prefs.setString(
      _pendingJobKey,
      jsonEncode({'server': widget.env.baseUrl, 'job_id': jobId}),
    );
    pendingPersisted = true;
  }

  Future<void> _forgetPending() async {
    if (!pendingPersisted) return;
    final prefs = await widget.env.sharedPreferences();
    await prefs.remove(_pendingJobKey);
    pendingPersisted = false;
  }

  Future<void> _resumePending() async {
    final prefs = await widget.env.sharedPreferences();
    final raw = prefs.getString(_pendingJobKey);
    if (raw == null) return;
    int? jobId;
    try {
      final saved = jsonDecode(raw);
      if (saved is! Map || saved['server'] != widget.env.baseUrl) return;
      jobId = saved['job_id'] as int?;
    } catch (_) {
      await prefs.remove(_pendingJobKey);
      return;
    }
    if (jobId == null || !mounted) return;
    pendingPersisted = true;
    setState(() {
      messages.add(_Msg.assistant('正在恢复上次健康询问的安全检查。'));
      busy = true;
      loadingStage = loadingMessages.length - 1;
    });
    _startLoadingStages(initialStage: loadingMessages.length - 1);
    try {
      final result = await _awaitDeferred({'accepted': true, 'job_id': jobId});
      await _showResult(result);
    } catch (e) {
      if (!mounted) return;
      _stopLoadingStages();
      setState(() {
        messages.add(_Msg.assistant(apiErrorMessage(e)));
        busy = false;
      });
    }
    _scrollDown();
  }

  Future<Map<String, dynamic>> _awaitDeferred(
    Map<String, dynamic> initial,
  ) async {
    if (initial['accepted'] != true) return initial;
    final jobId = initial['job_id'] as int?;
    if (jobId == null) {
      throw ApiException(ApiErrorKind.invalidResponse, '服务器返回内容无法识别');
    }
    for (var attempt = 0; attempt < 450; attempt += 1) {
      await Future<void>.delayed(const Duration(seconds: 2));
      if (!mounted) {
        throw ApiException(ApiErrorKind.server, '安全检查已在后台继续');
      }
      final status = await widget.env.client.getJobStatus(jobId);
      if (status['status'] == 'SUCCEEDED') {
        final result = status['result'];
        if (result is Map) return Map<String, dynamic>.from(result);
        await _forgetPending();
        throw ApiException(ApiErrorKind.invalidResponse, '服务器返回内容无法识别');
      }
      if (status['status'] == 'FAILED') {
        await _forgetPending();
        throw ApiException(ApiErrorKind.server, '安全检查未完成，请重新尝试');
      }
    }
    throw ApiException(ApiErrorKind.timeout, '安全检查仍在后台进行，请稍后重试');
  }

  Future<void> _showResult(Map<String, dynamic> r) async {
    await _forgetPending();
    if (!mounted) return;
    conversationId = r['conversation_id'] as int?;
    final answer = _Msg.assistant(
      r['direct_answer'] as String? ?? '',
      actions: ((r['actions'] as List?) ?? const []).map((e) => '$e').toList(),
      reason: r['reason'] as String?,
      medical: '${r['medical_review_state']}' == 'PERFORMED',
      grounded: ((r['evidence_ref'] as Map?)?['grounded'] == true),
      outOfScope: '${r['scope']}' == 'OUT_OF_SCOPE',
    );
    _stopLoadingStages();
    setState(() {
      messages.add(answer);
      busy = false;
    });
    pendingQuestion = null;
    pendingKey = null;
  }

  Future<void> _ask(String text) async {
    final q = text.trim();
    if (q.isEmpty || busy) return;
    controller.clear();
    setState(() {
      messages.add(_Msg.user(q));
      busy = true;
    });
    _startLoadingStages();
    _scrollDown();

    try {
      final key = pendingQuestion == q && pendingKey != null
          ? pendingKey!
          : _newSubmissionKey();
      pendingQuestion = q;
      pendingKey = key;
      final initial = await widget.env.client.qaAsk(
        q,
        conversationId: conversationId,
        idempotencyKey: key,
      );
      if (initial['accepted'] == true) {
        final jobId = initial['job_id'] as int?;
        if (jobId == null) {
          throw ApiException(ApiErrorKind.invalidResponse, '服务器返回内容无法识别');
        }
        await _rememberPending(jobId);
        if (mounted) {
          setState(() => loadingStage = loadingMessages.length - 1);
        }
      }
      final r = await _awaitDeferred(initial);
      await _showResult(r);
    } catch (e) {
      if (!mounted) return;
      _stopLoadingStages();
      setState(() {
        messages.add(_Msg.retry(apiErrorMessage(e), q));
        busy = false;
      });
    }
    _scrollDown();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: Column(
          children: [
          const Masthead(brand: 'PHE 问答', title: '问我的状态'),
          const Padding(
            padding: EdgeInsets.fromLTRB(24, 14, 24, 0),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text(
              '以你的身体状态为核心的决策助手。回答基于引擎的当前证据，不是通用聊天。',
              style: TextStyle(fontSize: 12.5, color: Ed.inkSoft, height: 1.6),
              ),
            ),
          ),
          Expanded(
            child: messages.isEmpty
                ? Center(
                    child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          const Text(
                            '可以这样问：',
                            style: TextStyle(color: Colors.black54),
                          ),
                          const SizedBox(height: 12),
                          Wrap(
                            spacing: 8,
                            runSpacing: 8,
                            alignment: WrapAlignment.center,
                            children: [
                              for (final s in suggestions)
                                ActionChip(
                                  label: Text(s),
                                  onPressed: () => _ask(s),
                                ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  )
                : ListView.builder(
                    controller: scrollController,
                    padding: const EdgeInsets.all(16),
                    itemCount: messages.length + (busy ? 1 : 0),
                    itemBuilder: (_, i) {
                      if (busy && i == messages.length) {
                        return Align(
                          alignment: Alignment.centerLeft,
                          child: Padding(
                            padding: const EdgeInsets.symmetric(vertical: 10),
                            child: Semantics(
                              liveRegion: true,
                              label: _loadingMessage,
                              child: Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  const SizedBox(
                                    width: 18,
                                    height: 18,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 2,
                                    ),
                                  ),
                                  const SizedBox(width: 10),
                                  Text(
                                    _loadingMessage,
                                    style: const TextStyle(
                                      fontSize: 13,
                                      color: Colors.black54,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ),
                        );
                      }
                      final m = messages[i];
                      return Align(
                        alignment: m.mine
                            ? Alignment.centerRight
                            : Alignment.centerLeft,
                        child: Container(
                          margin: const EdgeInsets.symmetric(vertical: 4),
                          padding: const EdgeInsets.symmetric(
                            horizontal: 14,
                            vertical: 10,
                          ),
                          constraints: BoxConstraints(
                            maxWidth: MediaQuery.of(context).size.width * 0.82,
                          ),
                          decoration: BoxDecoration(
                            color: m.mine
                                ? Theme.of(context).colorScheme.primary
                                : Colors.white,
                            borderRadius: BorderRadius.circular(14),
                            border: m.mine
                                ? null
                                : Border.all(color: Colors.black12),
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                m.text,
                                style: TextStyle(
                                  color: m.mine ? Colors.white : Colors.black87,
                                  height: 1.45,
                                  fontWeight: (!m.mine && m.isAnswer)
                                      ? FontWeight.w600
                                      : FontWeight.normal,
                                ),
                              ),
                              if (!m.mine && m.actions.isNotEmpty) ...[
                                const SizedBox(height: 6),
                                for (final a in m.actions)
                                  Padding(
                                    padding: const EdgeInsets.symmetric(
                                      vertical: 1,
                                    ),
                                    child: Row(
                                      mainAxisSize: MainAxisSize.min,
                                      children: [
                                        const Icon(
                                          Icons.check,
                                          size: 14,
                                          color: Colors.black45,
                                        ),
                                        const SizedBox(width: 4),
                                        Flexible(
                                          child: Text(
                                            a,
                                            style: const TextStyle(
                                              fontSize: 13,
                                            ),
                                          ),
                                        ),
                                      ],
                                    ),
                                  ),
                              ],
                              if (!m.mine && m.medical)
                                const Padding(
                                  padding: EdgeInsets.only(top: 6),
                                  child: Text(
                                    '本回答已按安全策略追加医学审查（不用于诊断）。',
                                    style: TextStyle(
                                      fontSize: 11,
                                      color: Colors.black45,
                                    ),
                                  ),
                                ),
                              if (!m.mine && m.grounded && !m.outOfScope)
                                const Padding(
                                  padding: EdgeInsets.only(top: 6),
                                  child: Text(
                                    '依据：你当前的个人证据包（非通用知识）',
                                    style: TextStyle(
                                      fontSize: 11,
                                      color: Colors.black38,
                                    ),
                                  ),
                                ),
                              if (m.retryQuestion != null)
                                TextButton.icon(
                                  onPressed: () => _ask(m.retryQuestion!),
                                  icon: const Icon(Icons.refresh, size: 16),
                                  label: const Text('重新尝试'),
                                ),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
          ),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: controller,
                      decoration: const InputDecoration(
                        hintText: '问一个和你身体状态有关的问题…',
                        border: OutlineInputBorder(),
                        isDense: true,
                        contentPadding: EdgeInsets.symmetric(
                          horizontal: 14,
                          vertical: 12,
                        ),
                      ),
                      onSubmitted: _ask,
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton.filled(
                    onPressed: busy ? null : () => _ask(controller.text),
                    icon: const Icon(Icons.send),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
      ),
    );
  }
}

class _Msg {
  final String text;
  final bool mine;
  final List<String> actions;
  final String? reason;
  final bool medical;
  final bool grounded;
  final bool outOfScope;
  final String? retryQuestion;

  _Msg.user(this.text)
    : mine = true,
      actions = const [],
      reason = null,
      medical = false,
      grounded = false,
      outOfScope = false,
      retryQuestion = null;

  _Msg.assistant(
    this.text, {
    this.actions = const [],
    this.reason,
    this.medical = false,
    this.grounded = false,
    this.outOfScope = false,
  }) : mine = false,
       retryQuestion = null;

  _Msg.retry(String message, String question)
    : text = message,
      mine = false,
      actions = const [],
      reason = null,
      medical = false,
      grounded = false,
      outOfScope = false,
      retryQuestion = question;

  bool get isAnswer => !mine;
}
