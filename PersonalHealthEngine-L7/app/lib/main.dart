/// Personal Health Engine — L7 mobile client.
///
/// Today-first / Conclusion-first. The app presents the engine's judgment verbatim; it
/// never re-derives state, never computes scores, and never stores model credentials.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api_client.dart';
import 'connection_store.dart';
import 'data_repository.dart';
import 'read_cache.dart';
import 'screens/history_screen.dart';
import 'screens/me_screen.dart';
import 'screens/patterns_screen.dart';
import 'screens/today_screen.dart';
import 'widgets/api_error_view.dart';

void main() {
  runApp(const HealthEngineApp());
}

class AppEnv extends ChangeNotifier {
  static const _initialToken = String.fromEnvironment('PHE_API_TOKEN');
  static const _configuredBaseUrl = String.fromEnvironment(
    'PHE_API_BASE_URL',
    defaultValue: ConnectionStore.productionBaseUrl,
  );

  String baseUrl;
  String token;
  late L7Client client;
  final ConnectionStore? _connectionStore;
  final SharedPreferences? _preferences;
  DataRepository? _repository;

  AppEnv({
    required this.baseUrl,
    required this.token,
    ConnectionStore? connectionStore,
    SharedPreferences? preferences,
  }) : _connectionStore = connectionStore,
       _preferences = preferences {
    client = HttpApiClient(baseUrl: baseUrl, token: token);
  }

  static String defaultBaseUrl() => _configuredBaseUrl;

  static Future<AppEnv> load() async {
    final prefs = await SharedPreferences.getInstance();
    final store = ConnectionStore(
      preferences: prefs,
      secrets: FlutterSecretStore(),
      initialToken: _initialToken,
    );
    final settings = await store.load();
    return AppEnv(
      baseUrl: settings.baseUrl,
      token: settings.token,
      connectionStore: store,
      preferences: prefs,
    );
  }

  Future<DataRepository> repository() async {
    final existing = _repository;
    if (existing != null) return existing;
    final prefs = _preferences ?? await SharedPreferences.getInstance();
    final created = DataRepository(ReadCache(prefs, serverId: baseUrl));
    _repository = created;
    return created;
  }

  Future<SharedPreferences> sharedPreferences() async =>
      _preferences ?? await SharedPreferences.getInstance();

  Future<void> updateConnection(String newUrl, String newToken) async {
    baseUrl = newUrl.trim();
    token = newToken.trim();
    client = HttpApiClient(baseUrl: baseUrl, token: token);
    _repository = null;
    final store = _connectionStore;
    if (store != null) await store.save(baseUrl, token);
    notifyListeners();
  }

  void notifyDataChanged() => notifyListeners();
}

class HealthEngineApp extends StatelessWidget {
  const HealthEngineApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Personal Health Engine',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF33557A)),
        scaffoldBackgroundColor: const Color(0xFFF7F8FA),
      ),
      home: const RootLoader(),
    );
  }
}

class RootLoader extends StatefulWidget {
  const RootLoader({super.key});
  @override
  State<RootLoader> createState() => _RootLoaderState();
}

class _RootLoaderState extends State<RootLoader> {
  AppEnv? env;
  Object? error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => error = null);
    try {
      final loaded = await AppEnv.load();
      if (mounted) setState(() => env = loaded);
    } catch (e) {
      if (mounted) setState(() => error = e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final e = env;
    if (e == null) {
      final failure = error;
      if (failure != null) {
        return Scaffold(
          body: Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: ApiErrorView(error: failure, onRetry: _load),
            ),
          ),
        );
      }
      return const _ShellSkeleton();
    }
    return HomeShell(env: e);
  }
}

class HomeShell extends StatefulWidget {
  final AppEnv env;
  const HomeShell({super.key, required this.env});
  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int index = 0;
  final List<Widget?> _screens = List<Widget?>.filled(4, null);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => unawaited(_prefetch()));
  }

  Widget _screen(int i) {
    return _screens[i] ??= switch (i) {
      0 => TodayScreen(env: widget.env),
      1 => HistoryScreen(env: widget.env),
      2 => PatternsScreen(env: widget.env),
      _ => MeScreen(env: widget.env),
    };
  }

  Future<void> _prefetch() async {
    final repository = await widget.env.repository();
    try {
      final history = await repository.refreshUsing(
        'history',
        client: widget.env.client,
        path: '/history/episodes?limit=30',
        fallback: widget.env.client.getEpisodes,
        versionOf: (data) => data['projection_version'] as int? ?? 1,
      );
      await repository.refreshUsing(
        'patterns',
        client: widget.env.client,
        path: '/patterns',
        fallback: widget.env.client.getPatterns,
        versionOf: (_) => DateTime.now().millisecondsSinceEpoch,
      );
      await repository.refreshUsing(
        'context',
        client: widget.env.client,
        path: '/context?limit=30',
        fallback: widget.env.client.listContext,
        versionOf: (_) => DateTime.now().millisecondsSinceEpoch,
      );
      final episodes = history?['episodes'] as List?;
      if (episodes != null && episodes.isNotEmpty) {
        final id = (episodes.first as Map)['id'] as int;
        await repository.refreshUsing(
          'timeline.$id',
          client: widget.env.client,
          path: '/history/episodes/$id?limit=30',
          fallback: () => widget.env.client.getEpisode(id),
          versionOf: (_) => DateTime.now().millisecondsSinceEpoch,
        );
      }
    } catch (_) {
      // Prefetch is opportunistic; each screen retains its own visible retry state.
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: IndexedStack(
        index: index,
        children: [
          for (var i = 0; i < 4; i++)
            i == index || _screens[i] != null
                ? _screen(i)
                : const SizedBox.shrink(),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (i) => setState(() => index = i),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.today_outlined),
            selectedIcon: Icon(Icons.today),
            label: '今日',
          ),
          NavigationDestination(
            icon: Icon(Icons.history_outlined),
            selectedIcon: Icon(Icons.history),
            label: '历史',
          ),
          NavigationDestination(
            icon: Icon(Icons.insights_outlined),
            selectedIcon: Icon(Icons.insights),
            label: '我的规律',
          ),
          NavigationDestination(
            icon: Icon(Icons.person_outline),
            selectedIcon: Icon(Icons.person),
            label: '我的',
          ),
        ],
      ),
    );
  }
}

class _ShellSkeleton extends StatelessWidget {
  const _ShellSkeleton();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('今日')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: const [
          _SkeletonBlock(height: 150),
          SizedBox(height: 12),
          _SkeletonBlock(height: 96),
          SizedBox(height: 12),
          _SkeletonBlock(height: 120),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: 0,
        destinations: const [
          NavigationDestination(icon: Icon(Icons.today), label: '今日'),
          NavigationDestination(
            icon: Icon(Icons.history_outlined),
            label: '历史',
          ),
          NavigationDestination(
            icon: Icon(Icons.insights_outlined),
            label: '我的规律',
          ),
          NavigationDestination(icon: Icon(Icons.person_outline), label: '我的'),
        ],
      ),
    );
  }
}

class _SkeletonBlock extends StatelessWidget {
  final double height;
  const _SkeletonBlock({required this.height});

  @override
  Widget build(BuildContext context) => Container(
    height: height,
    decoration: BoxDecoration(
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      borderRadius: BorderRadius.circular(12),
    ),
  );
}
