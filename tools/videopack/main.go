// videopack: comprime vídeos de aula para caber no limite do portal,
// mantendo 1080p. Orquestra o ffmpeg em paralelo controlado e reporta
// progresso em NDJSON para quem chamou (a interface do aula-uploader).
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"runtime"
	"syscall"
)

const versao = "1.0.0"

func main() {
	if len(os.Args) < 2 {
		uso()
		os.Exit(2)
	}
	// Ctrl+C ou SIGTERM (o Python manda isso ao cancelar) derruba os ffmpeg filhos.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	switch os.Args[1] {
	case "probe":
		os.Exit(cmdProbe(ctx, os.Args[2:]))
	case "pack":
		os.Exit(cmdPack(ctx, os.Args[2:]))
	case "version", "--version", "-v":
		fmt.Println(versao)
	default:
		uso()
		os.Exit(2)
	}
}

func uso() {
	fmt.Fprintf(os.Stderr, `videopack %s

  videopack probe <arquivos...>
      Lê duração, resolução e bitrate de cada arquivo (JSON no stdout).

  videopack pack [opções] <arquivos...>
      Comprime cada arquivo para caber no alvo, mantendo 1080p.
      Emite eventos NDJSON de progresso no stdout.

Opções de pack:
  --target-bytes   tamanho alvo em bytes (padrão 1 GiB)
  --max-bytes      teto rígido em bytes (padrão 1,15 GiB)
  --jobs           conversões simultâneas (padrão 2)
  --engine         hw (VideoToolbox, rápido) ou x264 (qualidade máxima)
  --out-suffix     sufixo do arquivo gerado (padrão " (comprimido)")
  --out-dir        pasta de saída (padrão: ao lado do original)
  --audio-bps      bitrate do áudio (padrão 192000)
`, versao)
}

func cmdProbe(ctx context.Context, argv []string) int {
	fs := flag.NewFlagSet("probe", flag.ContinueOnError)
	if err := fs.Parse(argv); err != nil {
		return 2
	}
	paths := fs.Args()
	if len(paths) == 0 {
		fmt.Fprintln(os.Stderr, "informe ao menos um arquivo")
		return 2
	}
	ffprobe, err := acharBinario("ffprobe")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 3
	}
	enc := json.NewEncoder(os.Stdout)
	for _, info := range ProbeMany(ctx, ffprobe, paths) {
		if err := enc.Encode(info); err != nil {
			return 1
		}
	}
	return 0
}

func cmdPack(ctx context.Context, argv []string) int {
	fs := flag.NewFlagSet("pack", flag.ContinueOnError)
	target := fs.Int64("target-bytes", 1<<30, "tamanho alvo em bytes")
	max := fs.Int64("max-bytes", 1234803097, "teto rígido em bytes")
	jobs := fs.Int("jobs", 1, "conversões simultâneas")
	engine := fs.String("engine", "hw", "hw ou x264")
	suffix := fs.String("out-suffix", " (comprimido)", "sufixo do arquivo gerado")
	outDir := fs.String("out-dir", "", "pasta de saída")
	audio := fs.Int64("audio-bps", 192000, "bitrate do áudio")
	if err := fs.Parse(argv); err != nil {
		return 2
	}
	paths := fs.Args()
	if len(paths) == 0 {
		fmt.Fprintln(os.Stderr, "informe ao menos um arquivo")
		return 2
	}
	ffmpeg, err := acharBinario("ffmpeg")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 3
	}
	ffprobe, err := acharBinario("ffprobe")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 3
	}
	if *jobs < 1 {
		*jobs = 1
	}
	if *jobs > runtime.NumCPU() {
		*jobs = runtime.NumCPU()
	}
	opts := PackOptions{
		TargetBytes: *target,
		MaxBytes:    *max,
		Jobs:        *jobs,
		Engine:      *engine,
		OutSuffix:   *suffix,
		OutDir:      *outDir,
		AudioBps:    *audio,
		FFmpeg:      ffmpeg,
		FFprobe:     ffprobe,
	}
	return Pack(ctx, opts, paths, NewEmitter(os.Stdout))
}

func acharBinario(nome string) (string, error) {
	caminho, err := exec.LookPath(nome)
	if err != nil {
		return "", fmt.Errorf("não encontrei o %s no PATH: instale com `brew install ffmpeg`", nome)
	}
	return caminho, nil
}
