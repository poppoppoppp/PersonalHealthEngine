/// Q&A — Personal Health Decision Assistant (§18–§22).
/// Answer-first. Health facts always come from the engine; the conversation only carries
/// dialogue. Medical review is applied by the sealed L6 policy when triggered.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../main.dart';
import '../widgets/api_error_view.dart';

class QnAScreen extends StatefulWidget {
  final AppEnv env;
  const QnAScreen({super.key, required this.env});
  @override
  State<QnAScreen> createState() => _QnAScreenState();
}

class _QnAScreenState extends State<QnAScreen> {
  final controller = TextEditingController();
  final scrollController = ScrollController();
  final List<_Msg> messages = [];
  int? conversationId;
  bool busy = false;
  int loadingStage = 0;
  Timer? loadingTimer;

  static const loadingMessages = [
    '正在理解你的问题',
    '正在结合你的健康数据',
    '正在进行安全检查（如需要）',
  ];

  static const suggestions = [
    '今天能不能练腿？',
    '今天适合跑步吗？',
    '昨晚睡差了，今天咖啡什么时候喝？',
    '今天适合安排高强度学习吗？',
  ];

  @override
  void dispose() {
    loadingTimer?.cancel();
    controller.dispose();
    scrollController.dispose();
    super.dispose();
  }

  void _startLoadingStages() {
    loadingTimer?.cancel();
    loadingStage = 0;
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
      final r = await widget.env.client.qaAsk(
        q,
        conversationId: conversationId,
      );
      conversationId = r['conversation_id'] as int?;
      final answer = _Msg.assistant(
        r['direct_answer'] as String? ?? '',
        actions: ((r['actions'] as List?) ?? const [])
            .map((e) => '$e')
            .toList(),
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
    } catch (e) {
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
      appBar: AppBar(title: const Text('问问我的状态')),
      body: Column(
        children: [
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: Text(
              '以你的身体状态为核心的决策助手。回答基于引擎的当前证据，不是通用聊天。',
              style: TextStyle(fontSize: 12, color: Colors.black45),
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
                              label: loadingMessages[loadingStage],
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
                                    loadingMessages[loadingStage],
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
