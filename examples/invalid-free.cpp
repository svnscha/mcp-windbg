// invalid-free.cpp
#include <cstdlib>

int main()
{
    int a;
    free(&a);
    return 0;
}
