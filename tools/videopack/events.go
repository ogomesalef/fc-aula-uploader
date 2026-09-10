package main

import (
	"encoding/json"
	"io"
	"sync"
)

// Emitter escreve eventos NDJSON (uma linha JSON por evento) para o Python ler
// em tempo real. Vários workers escrevem ao mesmo tempo, então o mutex garante
// que as linhas não se misturem.
type Emitter struct {
	mu  sync.Mutex
	out io.Writer
}

func NewEmitter(out io.Writer) *Emitter {
	return &Emitter{out: out}
}

type Event struct {
	Tipo       string  `json:"tipo"`
	Arquivo    string  `json:"arquivo,omitempty"`
	Nome       string  `json:"nome,omitempty"`
	Saida      string  `json:"saida,omitempty"`
	Pct        float64 `json:"pct,omitempty"`
	Fps        float64 `json:"fps,omitempty"`
	EtaS       float64 `json:"eta_s,omitempty"`
	Tentativa  int     `json:"tentativa,omitempty"`
	Tamanho    int64   `json:"tamanho,omitempty"`
	TamanhoOld int64   `json:"tamanho_antes,omitempty"`
	Largura    int     `json:"largura,omitempty"`
	Altura     int     `json:"altura,omitempty"`
	Duracao    float64 `json:"duracao,omitempty"`
	Total      int     `json:"total,omitempty"`
	Erro       string  `json:"erro,omitempty"`
	Aviso      string  `json:"aviso,omitempty"`
}

func (e *Emitter) Send(ev Event) {
	e.mu.Lock()
	defer e.mu.Unlock()
	line, err := json.Marshal(ev)
	if err != nil {
		return
	}
	e.out.Write(append(line, '\n'))
}
