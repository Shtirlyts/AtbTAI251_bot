<?php
error_log("PHP script started - DEBUG VERSION");
// logger.php - улучшенный логгер с дебаг информацией
header('Content-Type: text/html; charset=UTF-8');

// Московское время (UTC+3)
date_default_timezone_set('Europe/Moscow');

// Обработка POST запросов (новые логи)
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $input = file_get_contents('php://input');
    $data = json_decode($input, true);
    
    if ($data && isset($data['log'])) {
        $timestamp = date('Y-m-d H:i:s');
        $log_type = $data['type'] ?? 'unknown';
        $log_level = $data['level'] ?? 'info';
        $log_entry = "$timestamp - $log_type - " . strtoupper($log_level) . " - {$data['log']}\n";
        
        // Сохраняем в файл
        file_put_contents('bot_logs.txt', $log_entry, FILE_APPEND | LOCK_EX);
        error_log("Log written: " . trim($log_entry));
        echo "OK";
        exit;
    }
}

// Отображение логов (GET запросы)
$logs = [];
$total_lines = 0;
$displayed_lines = 0;

if (file_exists('bot_logs.txt')) {
    $logs = file('bot_logs.txt', FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    $total_lines = count($logs);
    $displayed_lines = min(100, $total_lines);
    $logs = array_slice($logs, -100); // Последние 100 строк
    $logs = array_reverse($logs); // Новые сверху
}

// Дебаг информация
$debug_info = [
    'total_lines_in_file' => $total_lines,
    'displayed_lines' => $displayed_lines,
    'file_size' => file_exists('bot_logs.txt') ? filesize('bot_logs.txt') : 0,
    'last_modified' => file_exists('bot_logs.txt') ? date('Y-m-d H:i:s', filemtime('bot_logs.txt')) : 'N/A',
    'current_time' => date('Y-m-d H:i:s')
];
?>
<!DOCTYPE html>
<html>
<head>
    <title>Bot Logs - DEBUG</title>
    <meta http-equiv="refresh" content="10">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        pre { background: white; padding: 15px; border-radius: 5px; border: 1px solid #ddd; max-height: 70vh; overflow-y: auto; }
        .timestamp { color: #666; }
        .info { color: #2ecc71; }
        .error { color: #e74c3c; font-weight: bold; }
        .warning { color: #f39c12; }
        .debug { color: #3498db; }
        .debug-info { background: #e8f4fd; padding: 10px; border-radius: 5px; margin-bottom: 15px; }
        .system { color: #9b59b6; }
    </style>
</head>
<body>
    <h1>🤖 Bot Logs - ATB TAI 251 - DEBUG</h1>
    
    <div class="debug-info">
        <strong>Debug Information:</strong><br>
        Total lines in file: <?= $debug_info['total_lines_in_file'] ?><br>
        Displayed lines: <?= $debug_info['displayed_lines'] ?><br>
        File size: <?= round($debug_info['file_size'] / 1024, 2) ?> KB<br>
        Last modified: <?= $debug_info['last_modified'] ?><br>
        Current time: <?= $debug_info['current_time'] ?>
    </div>
    
    <p>Автообновление каждые 10 секунд | Московское время | Новые сверху</p>
    
    <?php if (empty($logs)): ?>
        <pre>No logs found. Waiting for first log entry...</pre>
    <?php else: ?>
        <pre>
<?php foreach ($logs as $log): ?>
<?php
    // Парсим строку лога
    $parts = explode(' - ', $log, 4);
    if (count($parts) >= 4) {
        $timestamp = $parts[0];
        $log_type = $parts[1];
        $level = $parts[2];
        $message = $parts[3];
    } else {
        $timestamp = '';
        $level = 'UNKNOWN';
        $message = $log;
    }

    // Определяем класс для уровня
    $level_class = 'info';
    $level_upper = strtoupper($level);
    if (strpos($level_upper, 'ERROR') !== false || strpos($level_upper, 'CRITICAL') !== false) {
        $level_class = 'error';
    } elseif (strpos($level_upper, 'WARNING') !== false) {
        $level_class = 'warning';
    } elseif (strpos($level_upper, 'DEBUG') !== false) {
        $level_class = 'debug';
    } elseif (strpos($level_upper, 'SYSTEM') !== false) {
        $level_class = 'system';
    }
?>
<span class="timestamp"><?= htmlspecialchars($timestamp) ?></span> - <?= htmlspecialchars($log_type) ?> - <span class="<?= $level_class ?>"><?= htmlspecialchars($level) ?></span> - <?= htmlspecialchars($message) ?>

<?php endforeach; ?>
        </pre>
    <?php endif; ?>
    
    <p><small>Показывается <?= $displayed_lines ?> из <?= $total_lines ?> строк логов | IP: <?= $_SERVER['SERVER_ADDR'] ?? 'localhost' ?></small></p>
    
    <div style="margin-top: 20px;">
        <button onclick="location.reload()">Обновить вручную</button>
        <button onclick="if(confirm('Очистить все логи?')){ window.location.href='?clear=1'; }">Очистить логи</button>
    </div>
</body>
</html>

<?php
// Обработка очистки логов
if (isset($_GET['clear']) && $_GET['clear'] == 1) {
    if (file_exists('bot_logs.txt')) {
        file_put_contents('bot_logs.txt', '');
        echo "<script>alert('Логи очищены'); location.href='logger.php';</script>";
    }
}
?>