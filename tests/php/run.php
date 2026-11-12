<?php
// Plain-PHP test runner: php tests/php/run.php
// Any PHP notice/warning raised during a test counts as a failure.

error_reporting(E_ALL);
ini_set('display_errors', '1');

$GLOBALS['RT_TESTS'] = array();
$GLOBALS['RT_WARNINGS'] = array();

set_error_handler(function ($errno, $errstr, $errfile, $errline) {
	if (!(error_reporting() & $errno)) {
		return false;
	}
	$GLOBALS['RT_WARNINGS'][] = "$errstr at $errfile:$errline";
	return true;
});

function rt_test($name, $fn)
{
	$GLOBALS['RT_TESTS'][] = array($name, $fn);
}

function rt_fail($msg)
{
	throw new RuntimeException($msg);
}

function rt_assert_true($cond, $msg = 'expected true')
{
	if ($cond !== true) {
		rt_fail($msg);
	}
}

function rt_assert_false($cond, $msg = 'expected false')
{
	if ($cond !== false) {
		rt_fail($msg);
	}
}

function rt_assert_same($expected, $actual, $msg = '')
{
	if ($expected !== $actual) {
		rt_fail(($msg !== '' ? $msg . ': ' : '') . 'expected ' . var_export($expected, true) . ', got ' . var_export($actual, true));
	}
}

$root = dirname(__DIR__, 2);
require_once($root . '/www/html/includes/host_guard.php');
require_once($root . '/www/html/includes/access_jwt.php');
require_once($root . '/www/html/includes/request_gate.php');
require_once(__DIR__ . '/helpers.php');
require_once(__DIR__ . '/test_host_guard.php');
require_once(__DIR__ . '/test_access_jwt.php');
require_once(__DIR__ . '/test_request_gate.php');

$passed = 0;
$failed = 0;
foreach ($GLOBALS['RT_TESTS'] as $t) {
	list($name, $fn) = $t;
	$GLOBALS['RT_WARNINGS'] = array();
	$error = null;
	try {
		$fn();
	} catch (\Throwable $e) {
		$error = get_class($e) . ': ' . $e->getMessage() . ' (' . basename($e->getFile()) . ':' . $e->getLine() . ')';
	}
	if ($error === null && count($GLOBALS['RT_WARNINGS']) > 0) {
		$error = 'PHP warning/notice: ' . implode('; ', $GLOBALS['RT_WARNINGS']);
	}
	if ($error === null) {
		$passed++;
		echo "PASS  $name\n";
	} else {
		$failed++;
		echo "FAIL  $name\n      $error\n";
	}
}

th_cleanup();
echo "\n" . $passed . ' passed, ' . $failed . ' failed (PHP ' . PHP_VERSION . ")\n";
exit($failed > 0 ? 1 : 0);
