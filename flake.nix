{
  description = "Compare nix-instantiate evaluation stats between nixpkgs revisions or PRs";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      eachSystem = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
    in
    {
      packages = eachSystem (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          nix-eval-diff = pkgs.python3Packages.buildPythonApplication {
            pname = "nix-eval-diff";
            version = "0.1.0";
            format = "other";

            src = ./.;

            nativeBuildInputs = [ pkgs.makeWrapper ];

            installPhase = ''
              install -Dm755 compare-stats.py $out/bin/nix-eval-diff
              wrapProgram $out/bin/nix-eval-diff \
                --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.git pkgs.nix ]}
            '';
          };
        in
        {
          nix-eval-diff = nix-eval-diff;
          default = nix-eval-diff;
        }
      );
    };
}
