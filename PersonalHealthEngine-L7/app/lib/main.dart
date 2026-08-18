/// Personal Health Engine — L7 mobile client.
///
/// Today-first / Conclusion-first. The app presents the engine's judgment verbatim; it
/// never re-derives state, never computes scores, and never stores model credentials.
library;

import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api_client.dart';
import 'screens/history_screen.dart';
import 'screens/me_screen.dart';
import 'screens/patterns_screen.dart';
import 'screens/today_screen.dart';

void main() {
  runApp(const HealthEngineApp());
}

class AppEnv extends ChangeNotifier {
  String baseUrl;
  String token;
  late L7Client client;

  AppEnv({required this.baseUrl, required this.token}) {
    client = HttpApiClient(baseUrl: baseUrl, token: token);
  }

  static String defaultBaseUrl() =>
      kIsWeb ? 'http://127.0.0.1:8707' : 'http://10.0.2.2:8707';

  static Future<AppEnv> load() async {
    final prefs = await SharedPreferences.getInstance();
    return AppEnv(
      baseUrl: prefs.getString('server.baseUrl') ?? defaultBaseUrl(),
      token: prefs.getString('server.token') ?? 'dev-local-token',
    );
  }

  Future<void> updateConnection(String newUrl, String newToken) async {
    baseUrl = newUrl.trim();
    token = newToken.trim();
    client = HttpApiClient(baseUrl: baseUrl, token: token);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('server.baseUrl', baseUrl);
    await prefs.setString('server.token', token);
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

  @override
  void initState() {
    super.initState();
    AppEnv.load().then((e) => setState(() => env = e));
  }

  @override
  Widget build(BuildContext context) {
    final e = env;
    if (e == null) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
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
              label: '今日'),
          NavigationDestination(
              icon: Icon(Icons.history_outlined),
              selectedIcon: Icon(Icons.history),
              label: '历史'),
          NavigationDestination(
              icon: Icon(Icons.insights_outlined),
              selectedIcon: Icon(Icons.insights),
              label: '我的规律'),
          NavigationDestination(
              icon: Icon(Icons.person_outline),
              selectedIcon: Icon(Icons.person),
              label: '我的'),
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
