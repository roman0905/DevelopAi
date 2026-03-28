"""
延迟监控系统 - 记录各个模块的耗时

功能：
- 记录ASR/LLM/TTS/工具调用的耗时
- 按对话轮次汇总分析
- 输出可视化日志到/tmp目录
- 提供实时和最终统计报告
"""

import time
import json
import os
import threading
from typing import Dict, List, Optional
from collections import defaultdict, deque
from datetime import datetime
import csv


class LatencyMonitor:
    """延迟监控器 - 记录整个链路的耗时"""

    def __init__(self, tmp_dir: str = "/tmp"):
        self.tmp_dir = self._resolve_tmp_dir(tmp_dir)
        self.enabled = True
        
        # 确保tmp目录存在
        os.makedirs(self.tmp_dir, exist_ok=True)
        
        # 事件记录：每条事件包含turn_id, 模块名, 阶段, 耗时(秒)等
        self.events: List[Dict] = []
        self.lock = threading.RLock()
        
        # 按turn_id分组的事件
        self.turn_events: Dict[str, List[Dict]] = defaultdict(list)
        
        # 当前活跃的turn
        self.current_turn_id: Optional[str] = None
        
        # 各模块的统计数据
        self.module_stats: Dict[str, Dict] = defaultdict(lambda: {
            "总耗时": 0,
            "调用次数": 0,
            "平均耗时": 0,
            "最小耗时": float('inf'),
            "最大耗时": 0,
        })
        
        # 各turn的统计结果（用于汇总输出）
        self.turn_summaries: Dict[str, Dict] = {}
        
        # 用于计时的栈（嵌套计时）
        self.timing_stack: Dict[str, deque] = defaultdict(deque)

        self.events_file = os.path.join(self.tmp_dir, "latency_events.jsonl")
        self.realtime_file = os.path.join(self.tmp_dir, "latency_realtime.log")
        self._init_output_files()
        
        print(f"[延迟监控] 已启用，日志将保存到: {self.tmp_dir}")

    @staticmethod
    def _resolve_tmp_dir(tmp_dir: str) -> str:
        """统一解析监控输出目录，避免Windows下/tmp写到盘符根目录。"""
        if not tmp_dir:
            return os.path.abspath(os.path.join(os.getcwd(), "tmp"))

        normalized = tmp_dir.replace("\\", "/")
        if os.name == "nt" and normalized in ("/tmp", "tmp", "./tmp"):
            return os.path.abspath(os.path.join(os.getcwd(), "tmp"))

        if os.path.isabs(tmp_dir):
            return os.path.abspath(tmp_dir)

        return os.path.abspath(os.path.join(os.getcwd(), tmp_dir))

    def _init_output_files(self) -> None:
        """启动时创建日志文件并写入头信息"""
        started_at = datetime.now().isoformat()
        with open(self.events_file, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "event": "monitor_started",
                        "timestamp": started_at,
                        "tmp_dir": self.tmp_dir,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        with open(self.realtime_file, "a", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write(f"链路耗时监控启动: {started_at}\n")
            f.write("模块以秒为单位，记录每次对话中的关键阶段耗时\n")
            f.write("=" * 80 + "\n")

    def _append_event_files(self, event: Dict) -> None:
        """将每条事件实时追加到 /tmp 文件，便于直观查看"""
        with open(self.events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

        readable_line = (
            f"[{event['timestamp']}] "
            f"对话={event['turn_id']} | "
            f"模块={event['module']} | "
            f"阶段={event['stage']} | "
            f"耗时={event['elapsed_sec']}秒"
        )
        if event.get("details"):
            readable_line += f" | 说明={event['details']}"

        with open(self.realtime_file, "a", encoding="utf-8") as f:
            f.write(readable_line + "\n")

    def start_timer(self, conn_id: str, stage_name: str) -> float:
        """开始计时一个阶段
        
        Args:
            conn_id: 连接ID（用于区分不同的并发连接）
            stage_name: 阶段名称 (如: "ASR处理", "LLM推理", "TTS合成", "工具调用")
        
        Returns:
            开始时间戳（毫秒）
        """
        if not self.enabled:
            return time.time() * 1000
        
        with self.lock:
            start_time = time.time() * 1000
            self.timing_stack[conn_id].append({
                "stage": stage_name,
                "start": start_time
            })
            return start_time

    def end_timer(self, conn_id: str, stage_name: str, 
                  turn_id: Optional[str] = None,
                  details: Optional[str] = None) -> float:
        """结束计时一个阶段
        
        Args:
            conn_id: 连接ID
            stage_name: 阶段名称
            turn_id: 对话轮次ID
            details: 附加细节信息
        
        Returns:
            耗时（秒）
        """
        if not self.enabled:
            return 0
        
        with self.lock:
            end_time = time.time() * 1000
            
            if not self.timing_stack[conn_id]:
                return 0
            
            # 弹出最后一个计时记录
            timing = self.timing_stack[conn_id].pop()
            
            if timing["stage"] != stage_name:
                # 栈不匹配，跳过
                return 0
            
            start_time = timing["start"]
            elapsed_ms = end_time - start_time
            elapsed_sec = elapsed_ms / 1000.0
            
            # 设置当前turn（如果未指定）
            if turn_id is None:
                turn_id = self.current_turn_id or "unknown"
            
            # 记录事件
            event = {
                "timestamp": datetime.now().isoformat(),
                "turn_id": turn_id,
                "conn_id": conn_id,
                "module": self._parse_module(stage_name),
                "stage": stage_name,
                "elapsed_sec": round(elapsed_sec, 3),
                "details": details or "",
            }
            
            self.events.append(event)
            self.turn_events[turn_id].append(event)
            self._append_event_files(event)
            
            # 更新统计
            self._update_stats(event["module"], elapsed_sec)
            
            return elapsed_sec

    def set_turn_id(self, turn_id: str) -> None:
        """设置当前对话轮次ID"""
        with self.lock:
            self.current_turn_id = turn_id

    def record_event(self, conn_id: str, module: str, stage: str, 
                    elapsed_sec: float, turn_id: Optional[str] = None,
                    details: Optional[str] = None) -> None:
        """直接记录一个事件（不通过计时）
        
        Args:
            conn_id: 连接ID
            module: 模块名称 (ASR/LLM/TTS/工具调用)
            stage: 阶段名称
            elapsed_sec: 耗时（秒）
            turn_id: 对话轮次ID
            details: 附加细节
        """
        if not self.enabled:
            return
        
        with self.lock:
            if turn_id is None:
                turn_id = self.current_turn_id or "unknown"
            
            event = {
                "timestamp": datetime.now().isoformat(),
                "turn_id": turn_id,
                "conn_id": conn_id,
                "module": module,
                "stage": stage,
                "elapsed_sec": round(elapsed_sec, 3),
                "details": details or "",
            }
            
            self.events.append(event)
            self.turn_events[turn_id].append(event)
            self._update_stats(module, elapsed_sec)
            self._append_event_files(event)

    def generate_summary(self, output_format: str = "all") -> Dict:
        """生成汇总报告
        
        Args:
            output_format: "all", "csv", "json", "markdown"
        
        Returns:
            汇总数据字典
        """
        with self.lock:
            summary = {
                "timestamp": datetime.now().isoformat(),
                "total_events": len(self.events),
                "total_turns": len(self.turn_events),
                "module_summary": self._calculate_module_summary(),
                "turn_summaries": self._calculate_turn_summaries(),
            }
            
            if output_format in ["all", "markdown"]:
                self._save_markdown_report(summary)
            if output_format in ["all", "csv"]:
                self._save_csv_report(summary)
            if output_format in ["all", "json"]:
                self._save_json_report(summary)
            if output_format in ["all", "text"]:
                self._save_text_report(summary)
            
            return summary

    def _parse_module(self, stage_name: str) -> str:
        """从阶段名称推断模块名称"""
        if "ASR" in stage_name or "asr" in stage_name or "语音识别" in stage_name:
            return "语音识别(ASR)"
        elif "LLM" in stage_name or "llm" in stage_name or "大模型" in stage_name or "推理" in stage_name:
            return "大模型(LLM)"
        elif "TTS" in stage_name or "tts" in stage_name or "合成" in stage_name:
            return "语音合成(TTS)"
        elif "工具" in stage_name or "tool" in stage_name or "function" in stage_name:
            return "工具调用"
        else:
            return "其他"

    def _analyze_module_reason(self, module: str, avg_sec: float) -> str:
        """根据模块和平均耗时给出可能原因，辅助快速排查"""
        if module == "语音识别(ASR)":
            if avg_sec > 2.5:
                return "可能与音频片段过长、识别模型负载或网络ASR接口抖动有关"
            return "耗时正常，通常受音频长度与识别模型复杂度影响"
        if module == "大模型(LLM)":
            if avg_sec > 4.0:
                return "可能与模型推理负载、上下文过长、工具前置推理有关"
            return "耗时正常，通常受模型大小和上下文长度影响"
        if module == "语音合成(TTS)":
            if avg_sec > 2.0:
                return "可能与TTS服务响应慢、文本切分粒度或音频编码耗时有关"
            return "耗时正常，通常受文本长度与语音合成服务影响"
        if module == "工具调用":
            if avg_sec > 1.5:
                return "可能与外部接口网络时延、第三方服务响应时间有关"
            return "耗时正常，通常受外部依赖与I/O调用影响"
        return "建议结合阶段日志继续定位"

    def _update_stats(self, module: str, elapsed_sec: float) -> None:
        """更新模块统计数据"""
        stats = self.module_stats[module]
        stats["总耗时"] += elapsed_sec
        stats["调用次数"] += 1
        stats["平均耗时"] = stats["总耗时"] / stats["调用次数"]
        stats["最小耗时"] = min(stats["最小耗时"], elapsed_sec)
        stats["最大耗时"] = max(stats["最大耗时"], elapsed_sec)

    def _calculate_module_summary(self) -> Dict:
        """计算模块级别的汇总（全局统计）"""
        summary = {}
        for module, stats in self.module_stats.items():
            summary[module] = {
                "调用次数": stats["调用次数"],
                "总耗时(秒)": round(stats["总耗时"], 3),
                "平均耗时(秒)": round(stats["平均耗时"], 3),
                "最小耗时(秒)": round(stats["最小耗时"], 3) if stats["最小耗时"] != float('inf') else 0,
                "最大耗时(秒)": round(stats["最大耗时"], 3),
            }
        return summary

    def _calculate_turn_summaries(self) -> Dict:
        """计算每个turn的耗时汇总"""
        summaries = {}
        
        for turn_id, events in self.turn_events.items():
            turn_summary = defaultdict(lambda: {
                "耗时(秒)": 0,
                "调用次数": 0,
                "阶段数": 0,
            })
            
            for event in events:
                module = event["module"]
                turn_summary[module]["耗时(秒)"] += event["elapsed_sec"]
                turn_summary[module]["调用次数"] += 1
                turn_summary[module]["阶段数"] += 1
            
            # 转换为普通字典便于序列化
            summaries[turn_id] = {
                module: {
                    "耗时(秒)": round(data["耗时(秒)"], 3),
                    "调用次数": data["调用次数"],
                    "阶段数": data["阶段数"],
                    "平均耗时(秒)": round(data["耗时(秒)"] / data["调用次数"], 3) if data["调用次数"] > 0 else 0,
                }
                for module, data in turn_summary.items()
            }
        
        return summaries

    def _save_markdown_report(self, summary: Dict) -> None:
        """生成Markdown格式的报告"""
        filepath = os.path.join(self.tmp_dir, "latency_summary.md")
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("# 链路耗时监控报告\n\n")
            f.write(f"**生成时间**: {summary['timestamp']}\n\n")
            f.write(f"**总事件数**: {summary['total_events']}\n\n")
            f.write(f"**总对话轮数**: {summary['total_turns']}\n\n")
            
            # 模块统计
            f.write("## 模块统计（全局汇总）\n\n")
            f.write("| 模块 | 调用次数 | 总耗时(秒) | 平均耗时(秒) | 最小耗时(秒) | 最大耗时(秒) |\n")
            f.write("|------|--------|-----------|-----------|----------|----------|\n")
            
            for module, stats in summary["module_summary"].items():
                f.write(
                    f"| {module} | {stats['调用次数']} | {stats['总耗时(秒)']} | "
                    f"{stats['平均耗时(秒)']} | {stats['最小耗时(秒)']} | {stats['最大耗时(秒)']} |\n"
                )

            f.write("\n## 可能原因分析\n\n")
            for module, stats in summary["module_summary"].items():
                reason = self._analyze_module_reason(module, stats["平均耗时(秒)"])
                f.write(f"- {module}: {reason}\n")
            
            # 按turn统计
            if summary["turn_summaries"]:
                f.write("\n## 按对话轮次统计\n\n")
                
                for turn_id in sorted(summary["turn_summaries"].keys()):
                    turn_data = summary["turn_summaries"][turn_id]
                    f.write(f"\n### Turn {turn_id}\n\n")
                    f.write("| 模块 | 耗时(秒) | 调用次数 | 平均耗时(秒) |\n")
                    f.write("|------|--------|--------|----------|\n")
                    
                    for module, data in sorted(turn_data.items()):
                        f.write(
                            f"| {module} | {data['耗时(秒)']} | {data['调用次数']} | "
                            f"{data['平均耗时(秒)']} |\n"
                        )
        
        print(f"[延迟监控] Markdown报告已保存: {filepath}")

    def _save_csv_report(self, summary: Dict) -> None:
        """生成CSV格式的报告"""
        filepath = os.path.join(self.tmp_dir, "latency_summary.csv")
        
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["模块", "调用次数", "总耗时(秒)", "平均耗时(秒)", "最小耗时(秒)", "最大耗时(秒)"])
            
            for module, stats in summary["module_summary"].items():
                writer.writerow([
                    module,
                    stats['调用次数'],
                    stats['总耗时(秒)'],
                    stats['平均耗时(秒)'],
                    stats['最小耗时(秒)'],
                    stats['最大耗时(秒)'],
                ])
        
        print(f"[延迟监控] CSV报告已保存: {filepath}")

    def _save_json_report(self, summary: Dict) -> None:
        """生成JSON格式的报告"""
        filepath = os.path.join(self.tmp_dir, "latency_summary.json")
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        
        print(f"[延迟监控] JSON报告已保存: {filepath}")

    def _save_text_report(self, summary: Dict) -> None:
        """生成纯文本格式的报告"""
        filepath = os.path.join(self.tmp_dir, "latency_summary.txt")
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write("链路耗时监控报告\n")
            f.write("=" * 80 + "\n")
            f.write(f"生成时间: {summary['timestamp']}\n")
            f.write(f"总事件数: {summary['total_events']}\n")
            f.write(f"总对话轮数: {summary['total_turns']}\n")
            f.write("\n")
            
            f.write("=" * 80 + "\n")
            f.write("模块统计（全局汇总）\n")
            f.write("=" * 80 + "\n")
            
            for module, stats in summary["module_summary"].items():
                f.write(f"\n{module}\n")
                f.write(f"  调用次数: {stats['调用次数']}\n")
                f.write(f"  总耗时: {stats['总耗时(秒)']} 秒\n")
                f.write(f"  平均耗时: {stats['平均耗时(秒)']} 秒\n")
                f.write(f"  最小耗时: {stats['最小耗时(秒)']} 秒\n")
                f.write(f"  最大耗时: {stats['最大耗时(秒)']} 秒\n")
                f.write(f"  可能原因: {self._analyze_module_reason(module, stats['平均耗时(秒)'])}\n")
        
        print(f"[延迟监控] 纯文本报告已保存: {filepath}")

    def print_summary_to_console(self) -> None:
        """打印简要汇总到控制台"""
        with self.lock:
            summary = self._calculate_module_summary()
            
            print("\n" + "=" * 60)
            print("链路耗时统计汇总")
            print("=" * 60)
            
            for module, stats in summary.items():
                print(f"\n【{module}】")
                print(f"  调用次数: {stats['调用次数']}")
                print(f"  总耗时: {stats['总耗时(秒)']} 秒")
                print(f"  平均耗时: {stats['平均耗时(秒)']} 秒")
                print(f"  最小/最大耗时: {stats['最小耗时(秒)']}/{stats['最大耗时(秒)']} 秒")
            
            print("=" * 60 + "\n")


# 全局监控实例
_latency_monitor: Optional[LatencyMonitor] = None


def get_monitor() -> LatencyMonitor:
    """获取全局监控实例"""
    global _latency_monitor
    if _latency_monitor is None:
        _latency_monitor = LatencyMonitor()
    return _latency_monitor


def init_monitor(tmp_dir: str = "/tmp") -> LatencyMonitor:
    """初始化全局监控实例"""
    global _latency_monitor
    _latency_monitor = LatencyMonitor(tmp_dir=tmp_dir)
    return _latency_monitor
