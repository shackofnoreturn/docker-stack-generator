## Age encryption
AGE_PUBLIC_KEY="$HOME/.config/age/key.pub"
AGE_IDENTITY="$HOME/.config/age/key.pem"

encrypt() {
    [[ $# -eq 1 && -f "$1" ]] || {
        echo "Usage: encrypt <file>"
        return 1
    }

    age -R "$AGE_PUBLIC_KEY" -o "$1.age" "$1" || return 1
    echo "Encrypted: $1.age"
}
