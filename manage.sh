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
# 使用与Python应用相同的日志文件格式
DEFAULT_LOG_DIR="logs"

# 全局变量
SERVICE_NAME=""
VENV_PATH=""
PROJECT_DIR="$(pwd)"
PID_FILE=""
LOG_FILE=""
ENTRY_CMD=""
ENTRY_ARGS=""
PID_MATCH=""

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
        SERVICE_NAME=$(grep -E '^name\s*=' pyproject.toml | sed -E 's/^name\s*=\s*["\x27]([^"\x27]*)["\x27].*/\1/' | head -1)
    fi
    SERVICE_NAME=${SERVICE_NAME:-$DEFAULT_SERVICE_NAME}

    # 入口命令（本项目通过 CLI 启动）
    ENTRY_CMD="uv run anp-proxy"
    ENTRY_ARGS=""

    # PID 匹配关键字（用于查找运行中进程）
    PID_MATCH="anp-proxy"

    # 虚拟环境路径
    VENV_PATH=$DEFAULT_VENV_PATH

    # PID 文件路径
    PID_FILE="$PROJECT_DIR/.${SERVICE_NAME}.pid"

    # 日志目录路径（与Python应用保持一致）
    LOG_DIR="$DEFAULT_LOG_DIR"
    # Python应用会自己处理日志文件命名
    LOG_FILE="$LOG_DIR/anp_proxy_$(date +%Y%m%d).log"

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

    # 检查 config.toml 配置文件
    if [ ! -f "config.toml" ]; then
        print_error "config.toml 配置文件不存在！"
        print_info "ANP Proxy 需要 config.toml 配置文件才能运行"
        return 1
    else
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

    # 通过进程名查找
    pgrep -f "uv run.*anp-proxy" | head -1
}

# 启动服务
start_service() {
    print_info "启动 ANP Proxy..."

    # 检查是否已运行
    local pid=$(get_service_pid)
    if [ -n "$pid" ]; then
        print_warning "服务已在运行 (PID: $pid)"
        return 0
    fi

    # 检查项目依赖（静默）
    if ! uv sync --quiet 2>/dev/null; then
        print_warning "依赖可能需要更新，正在同步..."
        uv sync
    fi

    # 创建日志目录并检查权限
    if ! mkdir -p "$LOG_DIR" 2>/dev/null; then
        print_error "无法创建日志目录: $LOG_DIR"
        return 1
    fi

    if [ ! -w "$LOG_DIR" ]; then
        print_error "日志目录无写权限: $LOG_DIR"
        return 1
    fi

    # 启动服务（后台运行）
    print_info "在后台启动服务..."
    print_info "日志将输出到: $LOG_FILE"

    # 后台启动服务 - 让应用自己处理日志，只捕获启动错误
    nohup $ENTRY_CMD $ENTRY_ARGS >/dev/null 2>&1 &
    local service_pid=$!

    # 保存 PID
    echo "$service_pid" > "$PID_FILE"

    # 等待一下确认启动成功
    sleep 2

    # 验证服务是否成功启动
    if kill -0 "$service_pid" 2>/dev/null; then
        print_success "ANP Proxy 已启动 (PID: $service_pid)"
    else
        print_error "服务启动失败"
        rm -f "$PID_FILE"

        # 显示最近的错误日志
        if [ -f "$LOG_FILE" ]; then
            print_info "最近的日志:"
            tail -n 10 "$LOG_FILE"
        else
            print_warning "日志文件不存在: $LOG_FILE"
            print_info "请检查应用是否正常启动及日志配置"
        fi
        return 1
    fi
}

# 停止服务
stop_service() {
    print_info "停止 ANP Proxy..."

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
        print_success "ANP Proxy 已停止"
    else
        print_error "停止服务失败"
        return 1
    fi
}

# 重启服务
restart_service() {
    print_info "重启 ANP Proxy..."
    stop_service
    sleep 1
    start_service
}

# 显示状态
show_status() {
    echo -e "\n${BLUE}=== ANP Proxy 状态 ===${NC}"

    local pid=$(get_service_pid)
    if [ -n "$pid" ]; then
        echo -e "状态: ${GREEN}运行中${NC} (PID: $pid)"

        # 显示运行时间和内存
        if command_exists ps; then
            local info=$(ps -p "$pid" -o etime,rss --no-headers 2>/dev/null)
            if [ -n "$info" ]; then
                local etime=$(echo $info | awk '{print $1}')
                local mem=$(echo $info | awk '{print $2}')
                echo "运行时间: $etime"
                echo "内存使用: $((mem/1024)) MB"
            fi
        fi
    else
        echo -e "状态: ${RED}停止${NC}"
    fi

    echo "启动命令: $ENTRY_CMD"

    # 显示日志信息
    if [ -f "$LOG_FILE" ]; then
        local log_size=$(du -h "$LOG_FILE" | cut -f1)
        echo "今日日志: $LOG_FILE ($log_size)"
    elif [ -d "$LOG_DIR" ]; then
        local log_count=$(find "$LOG_DIR" -name "anp_proxy_*.log" -type f 2>/dev/null | wc -l)
        echo "日志目录: $LOG_DIR (共 $log_count 个日志文件)"
    else
        echo "日志目录: $LOG_DIR (不存在)"
    fi

    echo -e "${BLUE}==================${NC}"
}

# 显示日志
show_logs() {
    if [ -f "$LOG_FILE" ]; then
        print_info "显示最近的日志 (按 Ctrl+C 退出):"
        tail -f "$LOG_FILE"
    else
        print_warning "今日日志文件不存在: $LOG_FILE"
        print_info "正在查找其他日志文件..."

        # 查找日志目录中的最新日志文件
        if [ -d "$LOG_DIR" ]; then
            local latest_log=$(find "$LOG_DIR" -name "anp_proxy_*.log" -type f -exec ls -t {} + 2>/dev/null | head -1)
            if [ -n "$latest_log" ]; then
                print_info "找到最新日志: $latest_log"
                tail -f "$latest_log"
            else
                print_warning "未找到任何日志文件"
            fi
        else
            print_warning "日志目录不存在: $LOG_DIR"
        fi
    fi
}

# 显示帮助
show_help() {
    echo "ANP Proxy 管理脚本"
    echo ""
    echo "用法: $0 [start|stop|restart|status|logs]"
    echo ""
    echo "  start    启动服务"
    echo "  stop     停止服务"
    echo "  restart  重启服务"
    echo "  status   查看状态"
    echo "  logs     查看日志"
    echo ""
    echo "无参数运行显示交互菜单"
}

# 交互式菜单
show_menu() {
    clear
    echo -e "${BOLD}=== ANP Proxy 管理工具 ===${NC}"
    echo ""

    local pid=$(get_service_pid)
    if [ -n "$pid" ]; then
        echo -e "状态: ${GREEN}运行中${NC} (PID: $pid)"
    else
        echo -e "状态: ${RED}停止${NC}"
    fi

    echo ""
    echo "1) 查看状态    2) 启动服务    3) 停止服务"
    echo "4) 重启服务    5) 查看日志    0) 退出"
    echo ""
    echo -n "选择 [0-5]: "
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
        0)
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
