package main

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"sync"
)

// VideoInfo é o que o ffprobe consegue contar sobre um arquivo.
type VideoInfo struct {
	Arquivo string  `json:"arquivo"`
	Nome    string  `json:"nome"`
	Duracao float64 `json:"duracao"`
	Largura int     `json:"largura"`
	Altura  int     `json:"altura"`
	Bitrate int64   `json:"bitrate"`
	Codec   string  `json:"codec"`
	Audio   string  `json:"audio"`
	Tamanho int64   `json:"tamanho"`
	Erro    string  `json:"erro,omitempty"`
}

type ffprobeOutput struct {
	Streams []struct {
		CodecName string `json:"codec_name"`
		CodecType string `json:"codec_type"`
		Width     int    `json:"width"`
		Height    int    `json:"height"`
	} `json:"streams"`
	Format struct {
		Duration string `json:"duration"`
		Size     string `json:"size"`
		BitRate  string `json:"bit_rate"`
	} `json:"format"`
}

// Probe lê metadados de um arquivo. Erros viram campo Erro, não exceção: um
// arquivo corrompido no meio da lista não pode derrubar o lote inteiro.
func Probe(ctx context.Context, ffprobe string, path string) VideoInfo {
	info := VideoInfo{Arquivo: path, Nome: filepath.Base(path)}
	if st, err := os.Stat(path); err == nil {
		info.Tamanho = st.Size()
	}
	cmd := exec.CommandContext(ctx, ffprobe,
		"-v", "error",
		"-show_entries", "stream=codec_name,codec_type,width,height:format=duration,size,bit_rate",
		"-of", "json",
		path,
	)
	out, err := cmd.Output()
	if err != nil {
		info.Erro = "não consegui ler o vídeo: " + err.Error()
		return info
	}
	var parsed ffprobeOutput
	if err := json.Unmarshal(out, &parsed); err != nil {
		info.Erro = "resposta do ffprobe ilegível"
		return info
	}
	for _, s := range parsed.Streams {
		switch s.CodecType {
		case "video":
			if info.Largura == 0 {
				info.Largura, info.Altura, info.Codec = s.Width, s.Height, s.CodecName
			}
		case "audio":
			if info.Audio == "" {
				info.Audio = s.CodecName
			}
		}
	}
	info.Duracao, _ = strconv.ParseFloat(parsed.Format.Duration, 64)
	info.Bitrate, _ = strconv.ParseInt(parsed.Format.BitRate, 10, 64)
	if info.Tamanho == 0 {
		info.Tamanho, _ = strconv.ParseInt(parsed.Format.Size, 10, 64)
	}
	return info
}

// ProbeMany roda vários ffprobe ao mesmo tempo. ffprobe é leve (lê só cabeçalho),
// então o paralelismo aqui pode ser o número de núcleos sem incomodar a máquina.
func ProbeMany(ctx context.Context, ffprobe string, paths []string) []VideoInfo {
	results := make([]VideoInfo, len(paths))
	sem := make(chan struct{}, runtime.NumCPU())
	var wg sync.WaitGroup
	for i, p := range paths {
		wg.Add(1)
		go func(idx int, path string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			results[idx] = Probe(ctx, ffprobe, path)
		}(i, p)
	}
	wg.Wait()
	return results
}
