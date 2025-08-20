#!/bin/bash

# ANP Proxy 管理脚本（基于 UV）
# 用于启动、停止、重启、查看状态（针对本项目 anp-proxy 定制）

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# 默认配置
DEFAULT_SERVICE_NAME="anp-proxy"
DEFAULT_VENV_PATH=".venv"
DEFAULT_LOG_FILE="logs/anp-proxy.log"

# 全局变量
SERVICE_NAME=""
VENV_PATH=""
PROJECT_DIR="$(pwd)"
PID_FILE=""
ENTRY_CMD=""
ENTRY_ARGS=""
PID_MATCH=""

# 修复 sudo 环境下找不到用户级 uv 的问题
if [ -n "" ]; then
    SUDO_HOME=
    if [ -n "" ] && [ -d "/.local/bin" ]; then
        export PATH="/.local/bin:/home/ubuntu/.local/bin:/home/ubuntu/.cursor-server/bin/af58d92614edb1f72bdd756615d131bf8dfa5290/bin/remote-cli:/home/ubuntu/.local/bin:/home/ubuntu/.local/share/pnpm:/home/ubuntu/.cursor-server/bin/af58d92614edb1f72bdd756615d131bf8dfa5290/bin/remote-cli:/home/ubuntu/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin:/home/ubuntu/.cursor-server/extensions/ms-python.debugpy-2025.6.0-linux-x64/bundled/scripts/noConfigScripts"
    fi
fi
# 打印带颜色的消息
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# 初始化配置
init_config() {
    # 从 pyproject.toml 获取项目名称
    if [ -f "pyproject.toml" ]; then
        SERVICE_NAME=$(grep -E '^name\s*=' pyproject.toml | sed 's/name\s*=\s*["\x27]\([^"\x27]*\)["\x27]/\1/' | head -1)
    fi
    SERVICE_NAME=${SERVICE_NAME:-$DEFAULT_SERVICE_NAME}

    # 入口命令（本项目通过 CLI 启动）
    ENTRY_CMD="uv run anp-proxy"
    if [ -f "config.toml" ]; then
        ENTRY_ARGS="--config config.toml"
    else
        ENTRY_ARGS=""
    fi

    # PID 匹配关键字（用于查找运行中进程）
    PID_MATCH="anp-proxy"

    # 虚拟环境路径
    VENV_PATH=$DEFAULT_VENV_PATH

    # PID 文件路径
    PID_FILE="$PROJECT_DIR/.${SERVICE_NAME}.pid"
    LOG_FILE="$DEFAULT_LOG_FILE"
}

# 检查命令是否存在
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# 检查 uv 是否可用
check_uv() {
    if ! command_exists uv; then
        print_error "uv 未安装，请先安装 uv"
        print_info "安装命令: curl -LsSf https://astral.sh/uv/install.sh | sh"
        return 1
    fi
    return 0
}

# 检查项目是否已初始化
check_project() {
    if [ ! -f "pyproject.toml" ]; then
        print_warning "未检测到 pyproject.toml 文件"
        print_info "请确保在 anp-proxy 项目目录中运行此脚本"
        return 1
    fi

    # 非强制要求 config.toml，但若存在则提示
    if [ -f "config.toml" ]; then
        print_info "检测到配置文件: config.toml"
    fi

    return 0
}

# 获取进程 PID
get_service_pid() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE" 2>/dev/null)
        # 检查进程是否还在运行
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        else
            # 清理无效的 PID 文件
            rm -f "$PID_FILE"
        fi
    fi

    # 优先查找通过 screen 或 tmux 启动的进程
    local pid=""

    # 查找 screen 会话中的进程
    if command_exists screen && screen -list | grep -q "$SERVICE_NAME"; then
        # 通过 screen 会话名查找相关进程
        pid=$(pgrep -f "screen -dmS $SERVICE_NAME.*$ENTRY_CMD" | head -1)
        if [ -n "$pid" ]; then
            echo "$pid"
            return 0
        fi
    fi

    # 查找 tmux 会话中的进程
    if command_exists tmux && tmux has-session -t "$SERVICE_NAME" 2>/dev/null; then
        # 通过 tmux 会话名查找相关进程
        pid=$(pgrep -f "tmux.*$SERVICE_NAME.*$ENTRY_CMD" | head -1)
        if [ -n "$pid" ]; then
            echo "$pid"
            return 0
        fi
    fi

    # 通过进程名查找（匹配 anp-proxy 或 python -m anp_proxy）
    pgrep -af "uv run .*${PID_MATCH}|python -m anp_proxy|anp_proxy.cli:main|anp-proxy" | awk 'NR==1{print $1}'
}

# 启动服务
start_service() {
    print_info "启动 $SERVICE_NAME 服务..."

    # 检查是否已运行
    local pid=$(get_service_pid)
    if [ -n "$pid" ]; then
        print_warning "服务已在运行 (PID: $pid)"
        return 0
    fi

    # 确保项目依赖已安装
    print_info "检查项目依赖..."
    if ! uv sync; then
        print_error "依赖安装失败"
        return 1
    fi

    # 创建日志目录
    mkdir -p "$(dirname "$LOG_FILE")"

    # 启动服务（后台运行）
    print_info "在后台启动服务..."

    # 使用 nohup 启动服务
    # 统一环境：UTF-8 与禁用 ANSI 颜色，避免日志出现乱码与 \x1B 序列
    # 使用 setsid 确保进程在独立的会话中运行
    LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 PYTHONIOENCODING=UTF-8 NO_COLOR=1 ANP_NO_COLOR=1 \
    setsid nohup $ENTRY_CMD $ENTRY_ARGS > "$LOG_FILE" 2>&1 < /dev/null &
    local service_pid=$!

    # 保存 PID
    echo "$service_pid" > "$PID_FILE"

    # 等待一下确认启动成功
    sleep 2

    # 验证服务是否成功启动
    if kill -0 "$service_pid" 2>/dev/null; then
        print_success "服务已启动 (PID: $service_pid)"
        print_info "日志文件: $LOG_FILE"
    else
        print_error "服务启动失败"
        rm -f "$PID_FILE"

        # 显示错误日志
        if [ -f "$LOG_FILE" ]; then
            print_info "错误日志:"
            tail -n 50 "$LOG_FILE"
        fi
        return 1
    fi
}

# 停止服务
stop_service() {
    print_info "停止 $SERVICE_NAME 服务..."

    local pid=$(get_service_pid)
    if [ -z "$pid" ]; then
        print_warning "服务未运行"
        return 0
    fi

    # 尝试优雅停止
    print_info "发送 TERM 信号..."
    if kill "$pid" 2>/dev/null; then
        # 等待进程结束
        local count=0
        while [ $count -lt 10 ]; do
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 1
            count=$((count + 1))
        done

        # 如果还没结束，强制杀死
        if kill -0 "$pid" 2>/dev/null; then
            print_warning "优雅停止失败，强制终止..."
            kill -9 "$pid" 2>/dev/null
        fi

        # 清理 PID 文件
        rm -f "$PID_FILE"
        print_success "服务已停止"
    else
        print_error "停止服务失败"
        return 1
    fi
}

# 重启服务
restart_service() {
    print_info "重启 $SERVICE_NAME 服务..."
    stop_service
    sleep 1
    start_service
}

# 显示状态
show_status() {
    echo -e "\n${BLUE}=== $SERVICE_NAME 服务状态 ===${NC}"

    local pid=$(get_service_pid)
    if [ -n "$pid" ]; then
        echo -e "服务状态: ${GREEN}运行中${NC}"
        echo "进程 PID: $pid"

        # 显示进程信息
        if command_exists ps; then
            local proc_info=$(ps -p "$pid" -o pid,ppid,etime,rss --no-headers 2>/dev/null)
            if [ -n "$proc_info" ]; then
                echo "进程信息: $proc_info"
            fi
        fi

        # 显示内存使用
        if command_exists ps; then
            local mem_usage=$(ps -p "$pid" -o rss --no-headers 2>/dev/null)
            if [ -n "$mem_usage" ]; then
                echo "内存使用: $((mem_usage/1024)) MB"
            fi
        fi
    else
        echo -e "服务状态: ${RED}未运行${NC}"
    fi

    echo -e "\n项目信息:"
    echo "  项目目录: $PROJECT_DIR"
    echo "  启动命令: $ENTRY_CMD $ENTRY_ARGS"
    echo "  PID 文件: $PID_FILE"

    # 显示日志文件大小
    if [ -f "$LOG_FILE" ]; then
        local log_size=$(du -h "$LOG_FILE" | cut -f1)
        echo "  日志文件: $LOG_FILE ($log_size)"
    fi

    # 显示 uv 信息
    if command_exists uv; then
        echo "  uv 版本: $(uv --version 2>/dev/null | head -1)"
    fi

    echo -e "\n${BLUE}========================${NC}"
}

# 显示日志
show_logs() {
    if [ -f "$LOG_FILE" ]; then
        print_info "显示最近的日志 (按 Ctrl+C 退出):"
        tail -f "$LOG_FILE"
    else
        print_warning "日志文件不存在"
    fi
}

# 显示帮助
show_help() {
    echo "ANP Proxy 管理脚本"
    echo ""
    echo "用法: $0 [命令]"
    echo ""
    echo "命令:"
    echo "  start    - 启动服务"
    echo "  stop     - 停止服务"
    echo "  restart  - 重启服务"
    echo "  status   - 查看状态"
    echo "  logs     - 查看日志"
    echo "  help     - 显示帮助"
    echo ""
    echo "配置: 优先使用项目根目录下的 config.toml (如存在)"
    echo "入口: 通过 CLI 启动 -> uv run anp-proxy [--config config.toml]"
}

# 交互式菜单
show_menu() {
    clear
    echo -e "${BOLD}============================${NC}"
    echo -e "${BOLD}  UV Python Service 管理工具  ${NC}"
    echo -e "${BOLD}============================${NC}"
    echo ""

    # 显示当前状态
    echo -e "${YELLOW}当前状态：${NC}"
    echo -e "  项目名称: ${GREEN}$SERVICE_NAME${NC}"
    echo -e "  启动命令: ${GREEN}$ENTRY_CMD $ENTRY_ARGS${NC}"

    local pid=$(get_service_pid)
    if [ -n "$pid" ]; then
        echo -e "  运行状态: ${GREEN}运行中${NC} (PID: $pid)"

        # 显示内存使用
        if command_exists ps; then
            local mem_usage=$(ps -p "$pid" -o rss --no-headers 2>/dev/null)
            if [ -n "$mem_usage" ]; then
                echo -e "  内存使用: ${GREEN}$((mem_usage/1024)) MB${NC}"
            fi
        fi
    else
        echo -e "  运行状态: ${RED}未运行${NC}"
    fi

    echo ""
    echo -e "${BOLD}----------------------------${NC}"
    echo -e "${YELLOW}请选择操作：${NC}"
    echo ""
    echo "  1) 查看状态"
    echo "  2) 启动服务"
    echo "  3) 停止服务"
    echo "  4) 重启服务"
    echo "  5) 查看日志"
    echo "  6) 退出"
    echo ""
    echo -n "请输入选项 [1-6]: "
}

# 处理菜单选择
handle_menu_choice() {
    local choice=$1

    case $choice in
        1)
            echo ""
            show_status
            echo -n "按回车键继续..."
            read
            ;;
        2)
            echo ""
            start_service
            echo -n "按回车键继续..."
            read
            ;;
        3)
            echo ""
            stop_service
            echo -n "按回车键继续..."
            read
            ;;
        4)
            echo ""
            restart_service
            echo -n "按回车键继续..."
            read
            ;;
        5)
            echo ""
            print_info "显示日志 (按 q 退出):"
            if [ -f "$LOG_FILE" ]; then
                less +F "$LOG_FILE"
            else
                print_warning "日志文件不存在"
                echo -n "按回车键继续..."
                read
            fi
            ;;
        6)
            echo "退出管理工具"
            exit 0
            ;;
        *)
            print_error "无效选项"
            sleep 1
            ;;
    esac
}

# 主函数
main() {
    # 检查 uv
    if ! check_uv; then
        exit 1
    fi

    # 初始化配置
    init_config

    # 检查项目
    if ! check_project; then
        exit 1
    fi

    # 处理命令
    case "$1" in
        start)
            start_service
            ;;
        stop)
            stop_service
            ;;
        restart)
            restart_service
            ;;
        status)
            show_status
            ;;
        logs)
            show_logs
            ;;
        help|--help|-h)
            show_help
            ;;
        "")
            # 无参数时显示交互式菜单
            while true; do
                show_menu
                read choice
                handle_menu_choice "$choice"
            done
            ;;
        *)
            print_error "未知命令: $1"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

# 运行主函数
main "$@"
