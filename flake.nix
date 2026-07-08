{
  description = "AI Hold'em development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs = { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [
          claude-code

          python311
          pipenv

          nodejs_22

          postgresql_16

          docker-client
          docker-compose

          git
          gh
          curl
          jq
          pkg-config
          openssl
          zlib
        ];

        env = {
          PIPENV_VENV_IN_PROJECT = "1";
          PIPENV_IGNORE_VIRTUALENVS = "1";
          PIPENV_PYTHON = "${pkgs.python311}/bin/python";
          PYTHONPATH = ".";
          SECRET_KEY = "dev-secret";
          DATABASE_URL = "postgresql://aiholdem:aiholdem@localhost:5432/aiholdem";
          ARENA_PLAYERS = "ollama:deepseek-v4-pro,openrouter:google/gemini-2.5-flash,ollama:gemma4:31b";
          ARENA_PAUSE_ON_EMPTY = "true";
          LOG_LEVEL = "INFO";
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
            pkgs.openssl
            pkgs.postgresql_16
            pkgs.zlib
          ];
        };

        shellHook = ''
          echo "AI Hold'em dev shell"
          echo "Python: $(python --version)"
          echo "Node:   $(node --version)"
          echo
          echo "Setup:  pipenv install --dev && npm install"
          echo "Run:    pipenv run python run.py"
          echo "Test:   pipenv run pytest"
          echo "JS:     npx fast-check --test tests/property/test_card_renderer.js"
          echo "Claude: claude"
        '';
      };
    };
}
