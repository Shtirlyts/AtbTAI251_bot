<?php
// logger.php - красивый логгер с московским временем и правильным порядком
header('Content-Type: text/html; charset=UTF-8');

// Московское время (UTC+3)
date_default_timezone_set('Europe/Moscow');

// Обработка POST запросов (новые логи)
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $input = file_get_contents('php://input');
    $data = json_decode($input, true);
    
    if ($data && isset($data['log'])) {
        $timestamp = date('Y-m-d H:i:s');
        $log_entry = "$timestamp - __main__ - INFO - {$data['log']}\n";
        
        // Сохраняем в файл
        file_put_contents('bot_logs.txt', $log_entry, FILE_APPEND | LOCK_EX);
        echo "OK";
        exit;
    }
}

// Отображение логов (GET запросы) - старые сверху, новые снизу
$logs = [];
if (file_exists('bot_logs.txt')) {
    $logs = file('bot_logs.txt', FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    $logs = array_slice($logs, -100); // Последние 100 строк
}
?>
<!DOCTYPE html>
<html>
<head>
    <title>Bot Logs</title>
    <meta http-equiv="refresh" content="10">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        pre { background: white; padding: 15px; border-radius: 5px; border: 1px solid #ddd; }
        .timestamp { color: #666; }
        .info { color: #2ecc71; }
        .error { color: #e74c3c; }
        .warning { color: #f39c12; }
    </style>
</head>
<body>
    <h1>🤖 Bot Logs - ATB TAI 251</h1>
    <p>Автообновление каждые 10 секунд | Московское время</p>
    <pre>
<?php foreach ($logs as $log): ?>
<span class="timestamp"><?= htmlspecialchars(explode(' - ', $log)[0] ?? '') ?></span> - __main__ <span class="info">- INFO -</span> <?= htmlspecialchars(substr($log, strpos($log, '- INFO -') + 9) ?? $log) ?>

<?php endforeach; ?>
    </pre>
    <p><small>Последние 100 строк логов | IP: <?= $_SERVER['SERVER_ADDR'] ?? 'localhost' ?></small></p>
</body>
</html>