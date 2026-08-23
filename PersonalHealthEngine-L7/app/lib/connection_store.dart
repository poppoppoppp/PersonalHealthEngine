import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

abstract class SecretStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> delete(String key);
}

class FlutterSecretStore implements SecretStore {
  final FlutterSecureStorage storage;

  FlutterSecretStore([FlutterSecureStorage? storage])
    : storage = storage ?? const FlutterSecureStorage();

  @override
  Future<void> delete(String key) => storage.delete(key: key);

  @override
  Future<String?> read(String key) => storage.read(key: key);

  @override
  Future<void> write(String key, String value) =>
      storage.write(key: key, value: value);
}

class ConnectionSettings {
  final String baseUrl;
  final String token;

  const ConnectionSettings({required this.baseUrl, required this.token});
}

class ConnectionStore {
  static const productionBaseUrl = 'https://47.111.229.39';
  static const baseUrlKey = 'server.baseUrl';
  static const legacyTokenKey = 'server.token';
  static const tokenKey = 'phe.l7.bearer.v1';

  final SharedPreferences preferences;
  final SecretStore secrets;
  final String initialToken;

  ConnectionStore({
    required this.preferences,
    required this.secrets,
    required this.initialToken,
  });

  Future<ConnectionSettings> load() async {
    var token = (await secrets.read(tokenKey))?.trim() ?? '';
    if (token.isEmpty) {
      token = preferences.getString(legacyTokenKey)?.trim() ?? '';
      if (token.isEmpty) token = initialToken.trim();
      if (token.isNotEmpty) await secrets.write(tokenKey, token);
    }
    await preferences.remove(legacyTokenKey);
    return ConnectionSettings(
      baseUrl: _normalizeUrl(
        preferences.getString(baseUrlKey) ?? productionBaseUrl,
      ),
      token: token,
    );
  }

  Future<void> save(String baseUrl, String token) async {
    final normalizedUrl = _normalizeUrl(baseUrl);
    final normalizedToken = token.trim();
    await preferences.setString(baseUrlKey, normalizedUrl);
    if (normalizedToken.isEmpty) {
      await secrets.delete(tokenKey);
    } else {
      await secrets.write(tokenKey, normalizedToken);
    }
    await preferences.remove(legacyTokenKey);
  }

  String _normalizeUrl(String value) =>
      value.trim().replaceFirst(RegExp(r'/+$'), '');
}
