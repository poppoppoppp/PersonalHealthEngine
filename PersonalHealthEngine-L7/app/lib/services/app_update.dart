/// 应用内自更新：检查版本 → 带鉴权下载 → 拉起系统安装器。
/// APK 内含访问令牌，因此下载必须持令牌；安装由系统安装器完成（用户确认一次）。
library;

import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:open_filex/open_filex.dart';
import 'package:path_provider/path_provider.dart';

import '../api_client.dart';

class AppUpdateInfo {
  final int versionCode;
  final String versionName;
  final String notes;
  final String sha256;

  const AppUpdateInfo({
    required this.versionCode,
    required this.versionName,
    required this.notes,
    required this.sha256,
  });

  static AppUpdateInfo? fromJson(Map<String, dynamic> json) {
    if (json['available'] != true) return null;
    final code = int.tryParse('${json['version_code'] ?? ''}');
    if (code == null) return null;
    return AppUpdateInfo(
      versionCode: code,
      versionName: '${json['version_name'] ?? ''}',
      notes: '${json['notes'] ?? ''}',
      sha256: '${json['sha256'] ?? ''}',
    );
  }
}

class AppUpdateService {
  final L7Client client;

  AppUpdateService(this.client);

  /// 查询服务器上的最新版本；服务器未发布或请求失败返回 null。
  Future<AppUpdateInfo?> fetchLatest() async {
    final data = await client.getAppVersion();
    return AppUpdateInfo.fromJson(data);
  }

  /// 下载（持令牌）到应用缓存目录，返回本地文件路径。
  Future<String> downloadApk(AppUpdateInfo info) async {
    final bytes = await client.downloadAppApk();
    final dir = await getTemporaryDirectory();
    final file = File('${dir.path}/phe-update-${info.versionCode}.apk');
    await file.writeAsBytes(bytes, flush: true);
    return file.path;
  }

  /// 拉起系统安装器（用户需允许"安装未知应用"一次）。
  Future<void> install(String apkPath) async {
    final result = await OpenFilex.open(
      apkPath,
      type: 'application/vnd.android.package-archive',
    );
    if (result.type != ResultType.done) {
      throw AppUpdateException('无法启动安装：${result.message}');
    }
  }
}

class AppUpdateException implements Exception {
  final String message;
  const AppUpdateException(this.message);
  @override
  String toString() => message;
}

/// debug 断言辅助（避免未使用 import 警告）。
@visibleForTesting
void debugValidateUpdateInfo(AppUpdateInfo? info) {
  assert(info == null || info.versionCode > 0);
}
