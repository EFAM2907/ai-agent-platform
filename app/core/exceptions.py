# core/exceptions.py

class InvalidTokenError(Exception):
    """ se lanza cuando un JWT es inválido, está mal firmado, o ha expirado."""
    def __init__(self, message: str = "Invalid or expired token"):
        self.message = message
        super().__init__(self.message)
        
        
# core/exceptions.py

class InvalidCredentialsError(Exception):
    """Se lanza cuando el email no existe o el password no coincide.
    Mensaje genérico intencional: no revela cuál de los dos falló.(debo recordar poner el mismo mensaje en el service)"""
    
    def __init__(self, message: str = "Invalid email or password"):
        self.message = message
        super().__init__(self.message)