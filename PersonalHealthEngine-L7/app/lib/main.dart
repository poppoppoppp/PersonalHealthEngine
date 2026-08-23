/// Personal Health Engine — L7 mobile client.
///
/// Today-first / Conclusion-first. The app presents the engine's judgment verbatim; it
/// never re-derives state, never computes scores, and never stores model credentials.
library;

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api_client.dart';
import 'connection_store.dart';
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

  AppEnv({
    required this.baseUrl,
    required this.token,
    ConnectionStore? connectionStore,
  }) : _connectionStore = connectionStore {
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
    );
  }

  Future<void> updateConnection(String newUrl, String newToken) async {
    baseUrl = newUrl.trim();
    token = newToken.trim();
    client = HttpApiClient(baseUrl: baseUrl, token: token);
    final store = _connectionStore;
    if (store != null) await store.save(baseUrl, token);
    notifyListeners();
  }
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
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
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

  @override
  Widget build(BuildContext context) {
    final screens = [
      TodayScreen(env: widget.env),
      HistoryScreen(env: widget.env),
      PatternsScreen(env: widget.env),
      MeScreen(env: widget.env),
    ];
    return Scaffold(
      body: IndexedStack(index: index, children: screens),
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

/// Local cache helpers: last Today payload for instant open.
class TodayCache {
  static const _key = 'cache.today.v1';

  static Future<void> store(TodayPayload p) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_key, jsonEncode(p.raw));
  }

  static Future<TodayPayload?> read() async {
    final prefs = await SharedPreferences.getInstance();
    final s = prefs.getString(_key);
    if (s == null) return null;
    try {
      return TodayPayload((jsonDecode(s) as Map).cast<String, dynamic>());
    } catch (_) {
      return null;
    }
  }
}
