#!/usr/bin/env sh
set -eu

APP_NAME="${APP_NAME:-active-build}"
BSTYLE_APP_NAME="${BSTYLE_APP_NAME:-active-bstyle}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
PROJECT_DIR="${PROJECT_DIR:-}"
SKILL_PROJECT_ROOT="${SKILL_PROJECT_ROOT:-}"
SKILL_PLUGIN="${SKILL_PLUGIN:-}"
SKILL_INSTALL_DIR="${SKILL_INSTALL_DIR:-}"
SKILL_REL_PATH="${SKILL_REL_PATH:-skill/active-build}"

say() {
  printf '%s\n' "$*"
}

warn() {
  printf '! %s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

script_dir() {
  _src="$0"
  while [ -h "$_src" ]; do
    _dir="$(CDPATH= cd -- "$(dirname -- "$_src")" && pwd)"
    _link="$(readlink "$_src")"
    case "$_link" in
      /*) _src="$_link" ;;
      *) _src="$_dir/$_link" ;;
    esac
  done
  CDPATH= cd -- "$(dirname -- "$_src")" && pwd
}

expand_user_path() {
  case "$1" in
    "~") printf '%s\n' "$HOME" ;;
    "~/"*) printf '%s/%s\n' "$HOME" "${1#~/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

is_absolute_path() {
  case "$1" in
    /*) return 0 ;;
    *) return 1 ;;
  esac
}

prompt_required() {
  _prompt="$1"
  _default="${2:-}"
  _env_name="${3:-}"

  while :; do
    if [ -n "$_default" ]; then
      printf '? %s [%s]: ' "$_prompt" "$_default" >&2
    else
      printf '? %s: ' "$_prompt" >&2
    fi
    IFS= read -r _answer || true
    [ -n "$_answer" ] || _answer="$_default"
    if [ -n "$_answer" ]; then
      printf '%s\n' "$_answer"
      return 0
    fi
    if [ -n "$_env_name" ]; then
      warn "请输入 $_prompt，或通过 $_env_name 设置。"
    else
      warn "请输入 $_prompt。"
    fi
  done
}

normalize_skill_plugin() {
  case "$(printf '%s\n' "$1" | tr '[:upper:]' '[:lower:]')" in
    1|codex)
      printf '%s\n' "codex"
      ;;
    2|github|github-copilot|github_copilot|copilot)
      printf '%s\n' "github-copilot"
      ;;
    *)
      return 1
      ;;
  esac
}

skill_install_dir_for_plugin() {
  _project_root="$1"
  _plugin="$2"

  case "$_plugin" in
    codex)
      printf '%s\n' "$_project_root/.codex/skills"
      ;;
    github-copilot)
      printf '%s\n' "$_project_root/.github/skills"
      ;;
    *)
      return 1
      ;;
  esac
}

resolve_project_dir() {
  if [ -n "$PROJECT_DIR" ]; then
    PROJECT_DIR="$(expand_user_path "$PROJECT_DIR")"
  else
    PROJECT_DIR="$(script_dir)"
  fi
  is_absolute_path "$PROJECT_DIR" || die "PROJECT_DIR 必须是绝对路径。"
  [ -f "$PROJECT_DIR/cli/src/active_cli/active_common.py" ] || die "公共源码目录不完整：$PROJECT_DIR"
  [ -f "$PROJECT_DIR/cli/src/active_cli/active_build_cli.py" ] || die "active-build 源码目录不完整：$PROJECT_DIR"
  [ -f "$PROJECT_DIR/cli/src/active_cli/active_bstyle_cli.py" ] || die "active-bstyle 源码目录不完整：$PROJECT_DIR"
}

install_cli() {
  resolve_project_dir
  _source="$PROJECT_DIR/cli/bin/$APP_NAME"
  [ -f "$_source" ] || die "缺少 CLI 入口：$_source"
  _bstyle_source="$PROJECT_DIR/cli/bin/$BSTYLE_APP_NAME"
  [ -f "$_bstyle_source" ] || die "缺少 CLI 入口：$_bstyle_source"

  mkdir -p "$BIN_DIR"
  ln -sfn "$_source" "$BIN_DIR/$APP_NAME"
  say "已安装 CLI: $BIN_DIR/$APP_NAME -> $_source"
  ln -sfn "$_bstyle_source" "$BIN_DIR/$BSTYLE_APP_NAME"
  say "已安装 CLI: $BIN_DIR/$BSTYLE_APP_NAME -> $_bstyle_source"

  if ! has_cmd "$APP_NAME"; then
    warn "$BIN_DIR 可能不在 PATH 中。可加入 shell 配置：export PATH=\"$BIN_DIR:\$PATH\""
  fi
  if ! has_cmd "$BSTYLE_APP_NAME"; then
    warn "$BIN_DIR 可能不在 PATH 中。可加入 shell 配置：export PATH=\"$BIN_DIR:\$PATH\""
  fi
}

resolve_skill_install_dir() {
  if [ -n "$SKILL_INSTALL_DIR" ]; then
    _skill_install_dir="$(expand_user_path "$SKILL_INSTALL_DIR")"
    is_absolute_path "$_skill_install_dir" || die "SKILL_INSTALL_DIR 必须是绝对路径。"
    printf '%s\n' "$_skill_install_dir"
    return 0
  fi

  if [ -n "$SKILL_PROJECT_ROOT" ]; then
    _skill_project_root="$(expand_user_path "$SKILL_PROJECT_ROOT")"
    is_absolute_path "$_skill_project_root" || die "SKILL_PROJECT_ROOT 必须是绝对路径。"
    [ -d "$_skill_project_root" ] || die "SKILL_PROJECT_ROOT 指向的目录不存在：$_skill_project_root"
  else
    _skill_project_root="$(prompt_required "Active 项目根目录绝对路径" "" "SKILL_PROJECT_ROOT")"
    _skill_project_root="$(expand_user_path "$_skill_project_root")"
    is_absolute_path "$_skill_project_root" || die "Active 项目根目录必须是绝对路径。"
    [ -d "$_skill_project_root" ] || die "Active 项目根目录不存在：$_skill_project_root"
  fi

  if [ -n "$SKILL_PLUGIN" ]; then
    _skill_plugin="$(normalize_skill_plugin "$SKILL_PLUGIN")" \
      || die "SKILL_PLUGIN 只能是 codex 或 github-copilot。"
  else
    _skill_plugin="$(prompt_required "目标 Agent 插件 (1=Codex, 2=GitHub Copilot)" "" "SKILL_PLUGIN")"
    _skill_plugin="$(normalize_skill_plugin "$_skill_plugin")" \
      || die "目标 Agent 插件只能是 codex 或 github-copilot。"
  fi

  skill_install_dir_for_plugin "$_skill_project_root" "$_skill_plugin"
}

install_skill() {
  resolve_project_dir
  _skill_source="$PROJECT_DIR/$SKILL_REL_PATH"
  [ -f "$_skill_source/SKILL.md" ] || die "缺少 Skill：$_skill_source/SKILL.md"

  _skill_install_dir="$(resolve_skill_install_dir)"
  mkdir -p "$_skill_install_dir"
  ln -sfn "$_skill_source" "$_skill_install_dir/active-build"
  say "已安装 Skill: $_skill_install_dir/active-build -> $_skill_source"
}

print_version() {
  resolve_project_dir
  _version="-"
  [ -f "$PROJECT_DIR/VERSION" ] && _version="$(cat "$PROJECT_DIR/VERSION")"
  say "$APP_NAME installer version: $_version"
  say "source path: $PROJECT_DIR"
  say "common source: $PROJECT_DIR/cli/src/active_cli/active_common.py"
  say "active-build entry: $PROJECT_DIR/cli/src/active_cli/active_build_cli.py"
  say "active-bstyle entry: $PROJECT_DIR/cli/src/active_cli/active_bstyle_cli.py"
}

print_help() {
  cat <<EOF
active-build installer

用法:
  sh install.sh              安装 CLI 和 Skill
  sh install.sh cli          只安装/刷新 active-build 和 active-bstyle CLI
  sh install.sh skills       安装 Skill，并刷新 active-build 和 active-bstyle CLI
  sh install.sh version      查看版本和源码路径
  sh install.sh help         查看帮助

环境变量:
  PROJECT_DIR                active-build 工程目录；默认取 install.sh 所在目录
  BIN_DIR                    CLI 安装目录；默认 $HOME/.local/bin
  BSTYLE_APP_NAME            bstyle CLI 名称；默认 active-bstyle
  SKILL_PROJECT_ROOT         Active 项目根目录绝对路径
  SKILL_PLUGIN               codex 或 github-copilot，也支持 1 或 2
  SKILL_INSTALL_DIR          直接指定 skills 安装目录

默认 Skill 目录:
  Codex: <Active 项目根目录>/.codex/skills
  GitHub Copilot: <Active 项目根目录>/.github/skills
EOF
}

main() {
  _cmd="${1:-all}"
  case "$_cmd" in
    all|install)
      install_cli
      install_skill
      ;;
    cli)
      install_cli
      ;;
    skills|skill|install-skills|install-skill)
      install_skill
      install_cli
      ;;
    version|-v|--version)
      print_version
      ;;
    help|-h|--help)
      print_help
      ;;
    *)
      print_help
      die "未知命令：$_cmd"
      ;;
  esac
}

main "$@"
